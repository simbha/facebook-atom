#!/usr/bin/python
"""An App Engine app that provides "private" Atom feeds for your Facebook news
feed, ie posts from your friends.

Based on both plusstreamfeed and salmon-unofficial.
"""

__author__ = 'Ryan Barrett <facebook-atom@ryanb.org>'

import json
import logging
import os
import urllib
import urlparse

import appengine_config
from activitystreams import facebook
from activitystreams.webutil import util
from activitystreams.webutil import webapp2

from google.appengine.ext.webapp import template

OAUTH_SCOPES = facebook.OAUTH_SCOPES + ',offline_access'
GENERATED_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                       'templates', 'generated.html')
ATOM_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__),
                                  'activitystreams', 'templates', 'user_feed.atom')

API_HOME_URL = ('https://graph.facebook.com/me?access_token=%s&fields='
                'home.limit(50),id,username,name,link,updated_time,bio,location')

# based on salmon-unofficial/facebook.py.
# facebook api url templates. can't (easily) use urllib.urlencode() because i
# want to keep the %(...)s placeholders as is and fill them in later in code.
# TODO: use appengine_config.py for local mockfacebook vs prod facebook
GET_AUTH_CODE_URL = '&'.join((
    'https://www.facebook.com/dialog/oauth/?scope=%s' % OAUTH_SCOPES,
    'client_id=%(client_id)s',
    # redirect_uri here must be the same in the access token request!
    'redirect_uri=%(host_url)s/got_auth_code',
    'response_type=code',
    'state=%(state)s',
    ))

GET_ACCESS_TOKEN_URL = '&'.join((
    'https://graph.facebook.com/oauth/access_token?client_id=%(client_id)s',
    # redirect_uri here must be the same in the oauth request!
    # (the value here doesn't actually matter since it's requested server side.)
    'redirect_uri=%(host_url)s/got_auth_code',
    'client_secret=%(client_secret)s',
    'code=%(auth_code)s',
    ))


class GenerateHandler(webapp2.RequestHandler):
  """Registers the current user and generates a feed URL for their stream.

  Based on AddFacebook in salmon-unofficial/facebook.py.
  """

  def post(self):
    """Starts generating a feed URL by requesting a Facebook auth code.

    After retrieving an auth code, redirects to /facebook_got_auth_code,
    which makes the next request to get the access token.
    """
    logging.info('Generating a new feed. Asking FB for auth code.')

    url = GET_AUTH_CODE_URL % {
      'client_id': appengine_config.FACEBOOK_APP_ID,
      # TODO: CSRF protection identifier.
      # http://developers.facebook.com/docs/authentication/
      'host_url': self.request.host_url,
      'state': self.request.host_url + '/got_auth_token',
      }
    self.redirect(url)


class GotAuthCode(webapp2.RequestHandler):
  def get(self):
    """Gets an access token based on an auth code."""
    auth_code = self.request.get('code')
    assert auth_code
    logging.info('got auth code: %s', auth_code)

    redirect_uri = urllib.unquote(self.request.get('state'))
    assert '?' not in redirect_uri

    # TODO: handle permission declines, errors, etc
    url = GET_ACCESS_TOKEN_URL % {
      'auth_code': auth_code,
      'client_id': appengine_config.FACEBOOK_APP_ID,
      'client_secret': appengine_config.FACEBOOK_APP_SECRET,
      'host_url': self.request.host_url,
      }
    logging.info('getting access token via %s', url)
    resp = util.urlfetch(url)
    # TODO: error handling. handle permission declines, errors, etc
    logging.info('access token response: %s' % resp)
    params = urlparse.parse_qs(resp)
    access_token = params['access_token'][0]

    atom_url = '%s://%s/atom?access_token=%s' % (appengine_config.SCHEME,
                                                 appengine_config.HOST,
                                                 access_token)
    logging.info('generated feed URL: %s', atom_url)
    self.response.out.write(template.render(GENERATED_TEMPLATE_FILE,
                                            {'atom_url': atom_url}))


class AtomHandler(webapp2.RequestHandler):
  """Proxies the Atom feed for a Facebook user's stream.

  Authenticates to the Facebook API with the user's stored OAuth credentials.
  """
  def get(self):
    access_token = self.request.get('access_token')
    assert access_token
    resp = json.loads(util.urlfetch(API_HOME_URL % access_token))

    fb = facebook.Facebook(self)
    actor = fb.user_to_actor(resp)
    posts = resp.get('home', {}).get('data', [])
    activities = [fb.post_to_activity(p) for p in posts]

    # massage data
    for a in activities:
      obj = a.setdefault('object', {})
      who = a.get('actor', {})
      if 'content' not in obj and obj['objectType'] == 'image':
        obj['content'] = '%s added a new photo.' % (
          who.get('displayName') or who.get('username'))

    self.response.headers['Content-Type'] = 'text/xml'
    self.response.out.write(template.render(
        ATOM_TEMPLATE_FILE,
        {'title': 'Facebook news feed for %s' % actor['displayName'],
         'updated': activities[0]['object'].get('updated') if activities else '',
         'actor': actor,
         'items': activities,
         'host_url': self.request.host_url + "/",
         'request_url': self.request.url,
         }))


application = webapp2.WSGIApplication(
  [('/generate', GenerateHandler),
   ('/got_auth_code', GotAuthCode),
   ('/atom', AtomHandler),
   ],
  debug=appengine_config.DEBUG)
