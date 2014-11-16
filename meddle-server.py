#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import zmq
import random
import string
import logging
import json
import time
import sys
import datetime

def timestamp_str():
    return datetime.datetime.fromtimestamp(time.time()).strftime('%Y%m%d%H%M%S%f')

def publish(socket, timestamp, participant, channel, text):
    logging.debug("%s publishes to '%s': '%s'" % (participant, channel, text))
    socket.send_multipart([(channel + text).encode(), participant.encode()])
    with open('_%s.log' % channel, 'a') as f:
        f.write("%s: %s: %s: %s" % (timestamp, channel, participant, text))
        f.write("\n")

def random_string(chars):
    if not chars:
        chars = string.ascii_uppercase + string.digits
    return ''.join(
        random.choice(chars)
        for _ in range(N))

def uid():
    return "%x" % int(time.time()) + random_string(4)

def replace(in_str, src_characters, tgt_characters=' '):
    for c in src_characters:
        in_str = in_str.replace(c, tgt_characters)
    return in_str

def handle_tags(socket, channel, user, text):
    _contained_tags = [x.lower() for x in replace(text, '.,;?!:\'"').split(' ')
                       if len(x) > 1 and x[0] == '#']
    logging.info("tags mentioned: %s", _contained_tags)
    for t in _contained_tags:
        socket.send_multipart(
            tuple(str(x).encode()
                  for x in ("tag%s" % t, channel, user, text)))
        
    return _contained_tags

def store_tags(all_tags, tags, channel, user):
    """ 0: no changes, 
        1<<2: minor tagging (same channel and user), 
        1<<4: new tag on user,
        1<<6: new tag this day,
        1<<8: new tag on channel, 
        1<<16: new tag """
    _result = 0  
    if tags == []:
        return False
    for t in tags:
        if t not in all_tags:
            _result += 1<<16
            all_tags[t] = []
        all_tags[t].append((time.time(), channel, user))
        _result += 1<<2
    return _result

def publish_user_list(socket, users):
    socket.send_multipart(
            ["user_update".encode(),
             json.dumps(users.users_online()).encode()])

def publish_channel_list(socket, channels):
    socket.send_multipart(
            ["channels_update".encode(),
             json.dumps(
                 {x:list(y.participants) for x, y in channels.items()}).encode()])             

def publish_tags(socket, all_tags):
    socket.send_multipart(
            ["tags_update".encode(),
             json.dumps(all_tags).encode()])

def notify_user(socket, user_id, msg):
    socket.send_multipart(
        tuple(str(x).encode()
              for x in ("notify%d" % user_id,) + tuple(msg)))


class channel:
    def __init__(self):
        self.participants = set()
        self.tags = {}
        
    def add_participant(self, name):
        if not name in self.participants:
            self.participants.add(name) #todo: should be id
            return True
        return False

    def add_tags(self, tags):
        if len(tags) == 0: return False
        for t in tags:
            if not t in self.tags:
                self.tags[t] = []
            self.tags[t].append(time.time())
        return True
        
class user:
    def __init__(self):
        self.last_ping = time.time()

class user_container:

    def __init__(self):
        #[item for item in a if item[0] == 1]
        #[(id, item) for id, item in a.items() if item[1] == 'user2']
        # users (id, name, user)
        self._next_id = 0
        self._users_online = {}     # {id: (name, user)}
        self._associated_ids = {}   # {name: id}, permanent

    def find_or_create_name(self, name):
        #_result = [(id, item) for id, item in self._users_online.items() if item[1] == name]

        if name in self._associated_ids:
            _id = self._associated_ids[name]
        else:
            _id = self._next_id
            self._next_id += 1
            self._associated_ids[name] = _id

        _new_user = False
        if _id not in self._users_online:
            self._users_online[_id] = (name, user())
            _new_user = True
        _, _user = self._users_online[_id]
        return (_new_user, _id, _user)

    def get_name(self, id):
        """ returns name """
        if id in self._users_online:
            return self._users_online[id][0]
        return None

    def get_id(self, name):
        return self._associated_ids[name]

    def set_offline(self, user_ids):
        for i in user_ids: del self._users_online[i]

    def users_online(self):
        return [self._users_online[u][0] for u in self._users_online]

    def refresh(self, user_id):
        if not user_id in self._users_online:
            return False
        self._users_online[user_id][1].last_ping = time.time()
        return True

    def find_dead(self):
        _result = []
        _now = time.time()
        for _id, _user in self._users_online.items():
            if _now - _user[1].last_ping > 5:
                _result.append(_id)
        if len(_result) > 0:
            logging.info("users %s timeouted" % _result)
        return _result

def main():
    _users = user_container()
    _context = zmq.Context()

    _channels = {}
    _all_tags = {}
    _logs = {}
    _own_version = (0, 6, 0)
    _port_rpc = 32100
    _port_pub = 32101

    _rpc_socket = _context.socket(zmq.REP)
    _rpc_socket.bind("tcp://*:%d" % _port_rpc)

    _pub_socket = _context.socket(zmq.PUB)
    _pub_socket.bind("tcp://*:%d" % _port_pub)

    _poller = zmq.Poller()
    _poller.register(_rpc_socket, zmq.POLLIN)
    logging.info("meddle version: %s", _own_version)
    logging.info("using Python version %s", tuple(sys.version_info))
    logging.info("meddle server listening on port %d, sending on port %d",
                 _port_rpc, _port_pub)

    while True:

        try:
            dead_users = _users.find_dead()
            _users.set_offline(dead_users)
            if not dead_users == []:
                publish_user_list(_pub_socket, _users)

            if _poller.poll(3000) == []:
                logging.debug("waiting..")
                continue

            _message = _rpc_socket.recv_string()
            # logging.debug("got '%s' (%s)" % (_message, type(_message)))

            if _message == "hello":
                _answer = json.loads(_rpc_socket.recv_string())
                _name = _answer['name']
                _version = _answer['version']
                logging.debug("hello from '%s' with client version %s" % (
                    _name, _version))
                if tuple(_version) != tuple(_own_version):
                    _rpc_socket.send_string(json.dumps({'accepted': False,
                                                        'version': _own_version}))
                else:
                    _is_new, _id, _user = _users.find_or_create_name(_name)
    
                    _rpc_socket.send_string(json.dumps({'accepted': True,
                                                        'id': _id,
                                                        'version': _own_version,
                                                        'sub_port': _port_pub}))
                    
                    if _is_new:
                         # todo: send only update-info
                        publish_user_list(_pub_socket, _users)

            elif _message == "create_channel":
                _sender_id = int(_rpc_socket.recv_string())
                _invited_users = json.loads(_rpc_socket.recv_string())
                _name = _users.get_name(_sender_id)
                if not _name:
                    logging.warn("user with id %d marked offline but sending",
                                 _sender_id)
                    _rpc_socket.send_string("nok")
                else:
                    logging.debug("%s creates channel and invites '%s'",
                                  _sender_id, _invited_users)
                    _channel_name = random_string(10)
                    # todo - check collisions
                    _rpc_socket.send_string(_channel_name)
                    _channels[_channel_name] = channel()
                    _channels[_channel_name].participants.add(_name)
                    for uid in [_users.get_id(u) for u in _invited_users]:
                        notify_user(_pub_socket,
                                    uid, ('join_channel', _channel_name))
                    publish_channel_list(_pub_socket, _channels)

            elif _message == "get_channels":
                _rpc_socket.send_string(
                    json.dumps(
                        {x:list(y.participants) for x, y in _channels.items()}))

            elif _message == "get_users":
                _rpc_socket.send_string(json.dumps(_users.users_online()))

            elif _message == "get_active_tags":
                _rpc_socket.send_string(json.dumps(_all_tags))

            elif _message.startswith("get_log"):
                _channel = _rpc_socket.recv_string()
                if _channel in _logs:
                    _rpc_socket.send_string(json.dumps(_logs[_channel]))
                else:
                    _rpc_socket.send_string(json.dumps([]))

            elif _message.startswith("ping"):
                # todo: handle users
                _sender_id = int(_rpc_socket.recv_string())
                if _users.refresh(_sender_id):
                    _rpc_socket.send_string('ok')
                else:
                    logging.warn("user with id %d marked offline but sending",
                                 _sender_id)
                    _rpc_socket.send_string('nok')

            elif _message == "publish":
                _sender_id = int(_rpc_socket.recv_string())
                _channel = _rpc_socket.recv_string()
                _text = _rpc_socket.recv_string()
                _name = _users.get_name(_sender_id)
                if not _name:
                    logging.warn("user with id %d marked offline but sending",
                                 _sender_id)
                    _rpc_socket.send_string("nok")
                elif _channel not in _channels:
                    logging.warn("tried to send on channel %s which is currently not known",
                                 _channel)
                    _rpc_socket.send_string("nok")
                else:
                    _rpc_socket.send_string('ok')
                    # todo: handle wrong user
                    if _channels[_channel].add_participant(_name):
                        publish_channel_list(_pub_socket, _channels)
                    _tags = handle_tags(_pub_socket, _channel, _name, _text)
                    #if _channels[_channel].add_tags(_tags):
                    if store_tags(_all_tags, _tags, _channel, _sender_id) > 0: #1<<2:
                        publish_tags(_pub_socket, _all_tags)
                    publish(_pub_socket, timestamp_str(), _name, _channel, _text)
                    if not _channel in _logs:
                        _logs[_channel] = []
                    _logs[_channel].append(("", _name, _text))

        except Exception as ex:
            logging.error("something bad happened: %s", ex)
            time.sleep(3)
            raise

if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s (%(thread)d) %(levelname)s %(message)s",
        datefmt="%y%m%d-%H%M%S",
        level=logging.DEBUG)
    logging.addLevelName(logging.CRITICAL, "(CRITICAL)")
    logging.addLevelName(logging.ERROR,    "(EE)")
    logging.addLevelName(logging.WARNING,  "(WW)")
    logging.addLevelName(logging.INFO,     "(II)")
    logging.addLevelName(logging.DEBUG,    "(DD)")
    logging.addLevelName(logging.NOTSET,   "(NA)")

    main()
