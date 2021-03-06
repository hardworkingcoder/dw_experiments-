import urllib
import os
import uuid
import requests
import stripe
import json
import flask
from flask import Flask, render_template, request, redirect, session, url_for, g
from flask.ext.sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_failsafe import failsafe
import calendar
import time
import jinja2
app = Flask(__name__, static_url_path='/static')
app.config['PROPAGATE_EXCEPTIONS'] = True
app.jinja_loader = jinja2.FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
socketio = SocketIO(app)
from threading import Thread
thread = None
from flask import send_from_directory
app.config.from_pyfile('_config.py')
db = SQLAlchemy(app)
import models
from sqlalchemy import and_
import calendar
import time
from flask_oauth import OAuth
import ast
oauth = OAuth()
from sqlalchemy.orm.attributes import flag_modified
import uuid
from flask import jsonify
import requests
from flask_login import LoginManager, UserMixin, login_user, logout_user,\
    current_user
from datadotworld.config import DefaultConfig
from datadotworld.datadotworld import DataDotWorld
import datadotworld

class InlineConfig(DefaultConfig):
    def __init__(self, token):
        super(InlineConfig, self).__init__()
        self._auth_token = token

login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(session_token):
    return models.User.query.filter_by(session_token=session_token).first()

@app.route('/login', strict_slashes=False)
def login():
    location = 'https://data.world/oauth/authorize?client_id=%s&redirect_uri=https://dw_experiments_dev.hardworkingcoder.com/dwoauth&response_type=code' % app.config['DATADOTWORLD_CLIENT_ID']
    return flask.redirect(location, code=302)

def get_access_info(code):
    params = {
      'code': code,
      'client_id': app.config['DATADOTWORLD_CLIENT_ID'],
      'client_secret': app.config['DATADOTWORLD_CLIENT_SECRET'].replace('#', '%23'),
      'grant_type': 'authorization_code'
    }
    params_as_str = '&'.join(['='.join(pair) for pair in params.items()])
    url = 'https://data.world/oauth/access_token?%s' % (params_as_str)
    response = requests.post(url)
    return response.json()

def get_user_info(access_token):
    url = "https://api.data.world/v0/user"
    payload = "{}"
    headers = {'authorization': 'Bearer <<%s>>' % (access_token)}
    response = requests.request("GET", url, data=payload, headers=headers)
    return response.json()

def update_db_with_access_and_user_info(access_info, user_info):
    user_exists = db.session.query(models.User.social_id).filter_by(social_id=user_info['id']).scalar() is not None
    if user_exists:
        user = models.User.query.filter_by(social_id=user_info['id']).first()
        user.ddw_access_token = access_info['access_token']
        user.ddw_token_expires_in = access_info['expires_in']
        user.ddw_avatar_url = user_info['avatarUrl']
        user.nickname = user_info['displayName']
        user.ddw_user_updated = user_info['updated']
        db.session.commit()
    else:
        user = models.User(ddw_access_token=access_info['access_token'], ddw_token_expires_in=access_info['expires_in'], ddw_avatar_url=user_info['avatarUrl'], nickname=user_info['displayName'], social_id=user_info['id'], ddw_user_created=user_info['created'], ddw_user_updated=user_info['updated'])
        db.session.add(user)
        db.session.commit()
    return user

row2dict = lambda r: {c.name: str(getattr(r, c.name)) for c in r.__table__.columns}

@app.route('/api/get_users_info')
def get_users_info():
    return jsonify(row2dict(load_user(session['user_id'])))

@app.route('/dwoauth', strict_slashes=False)
def dwoauth():
    access_info = get_access_info(request.args.get('code'))
    user_info = get_user_info(access_info['access_token'])
    user = update_db_with_access_and_user_info(access_info, user_info)
    session.clear()
    login_user(user, True)
    return flask.redirect('/', code=302)

@app.route('/logout', strict_slashes=False)
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/api/list_users_datasets')
def list_users_datasets():
    import requests

    url = "https://api.data.world/v0/user/datasets/own"

    payload = "{}"
    headers = {'authorization': 'Bearer <<%s>>' % (load_user(session['user_id']).ddw_access_token)}

    response = requests.request("GET", url, data=payload, headers=headers)

    return jsonify(json.loads(response.text))

@app.route('/', strict_slashes=False)
def index():
    return send_from_directory('static', 'index.html')

def get_ddw(user_id):
    token = load_user(user_id).ddw_access_token
    ddw = DataDotWorld(config=InlineConfig(token))
    ddw_client = ddw.api_client
    return ddw, ddw_client

@app.route('/api/create_dataset', strict_slashes=False, methods=['POST'])
def create_dataset():
    ddw, ddw_client = get_ddw(session['user_id'])
    owner = load_user(session['user_id']).social_id
    return ddw_client.create_dataset(owner, title=request.form['title'], license=request.form['license'], visibility=request.form['visibility'])

@app.route('/api/delete_file', strict_slashes=False, methods=['POST'])
def delete_file():
    ddw, ddw_client = get_ddw(session['user_id'])
    ddw_client.delete_files('%s/%s' % (request.form['owner'], request.form['id']), [request.form['filename']])
    return jsonify({'success': True})

def change_to_csv(filename):
    filename = filename.lower()
    index_of_dot = filename.index('.')
    return filename[:index_of_dot] + '.csv'

@app.route('/api/rename_file', strict_slashes=False, methods=['POST'])
def rename_file():
    ddw, ddw_client = get_ddw(session['user_id'])
    
    from os.path import expanduser, join
    home = expanduser("~")
    local_ddw_data = join(home, '.dw/cache/%s/%s/latest/data/' % (request.form['owner'], request.form['id']))
    ddw.load_dataset('%s/%s' % (request.form['owner'], request.form['id']), force_update=True)
    os.rename(join(local_ddw_data, change_to_csv(request.form['filename'])), join(local_ddw_data, change_to_csv(request.form['new_filename'])))
    ddw_client.delete_files('%s/%s' % (request.form['owner'], request.form['id']), [request.form['filename']])
    ddw_client.upload_files('%s/%s' % (request.form['owner'], request.form['id']), [join(local_ddw_data, change_to_csv(request.form['new_filename']))])
    return jsonify({'success': True})

@app.route('/api/move_file', strict_slashes=False, methods=['POST'])
def move_file():
    ddw, ddw_client = get_ddw(session['user_id'])
    
    from os.path import expanduser, join
    home = expanduser("~")
    local_ddw_data = join(home, '.dw/cache/%s/%s/latest/data/' % (request.form['owner'], request.form['current_id']))
    ddw.load_dataset('%s/%s' % (request.form['owner'], request.form['current_id']), force_update=True)
    ddw_client.upload_files('%s/%s' % (request.form['owner'], request.form['target_id']), [join(local_ddw_data, change_to_csv(request.form['filename']))])
    ddw_client.delete_files('%s/%s' % (request.form['owner'], request.form['current_id']), [request.form['filename']])
    return jsonify({'success': True})

@app.route('/api/upload_file', strict_slashes=False, methods=['POST'])
def upload_file():
    print 'UF', request.form
    print 'R', request.files
    ddw, ddw_client = get_ddw(session['user_id'])
    
    from os.path import expanduser, join
    home = expanduser("~")
    file = request.files['file_0']
    # if user does not select file, browser also
    # submit a empty part without filename
    if file.filename == '':
        jsonify({'success': False})
    if file:
        filename = file.filename
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    ddw_client.upload_files('%s/%s' % (request.form['owner'], request.form['id']), [join(app.config['UPLOAD_FOLDER'], filename)])
    return jsonify({'success': True})

@failsafe
def create_app():
    return app

import eventlet
eventlet.monkey_patch()
if __name__ == '__main__':
    socketio.run(create_app(), debug=True, port=5000)
