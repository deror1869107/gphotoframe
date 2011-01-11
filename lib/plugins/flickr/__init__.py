# -*- coding: utf-8 -*-
#
# Flickr plugin for GNOME Photo Frame
# Copyright (c) 2009-2010, Yoshizumi Endo <y-endo@ceres.dti.ne.jp>
# Licence: GPL3

import random
import time
import json
from gettext import gettext as _

from ..base import *
from ...utils.iconimage import WebIconImage
from ...utils.config import GConf
from ...utils.urlgetautoproxy import UrlGetWithAutoProxy
from api import *
from authdialog import *

def info():
    return [FlickrPlugin, FlickrPhotoList, PhotoSourceFlickrUI, PluginFlickrDialog]

class FlickrPlugin(base.PluginBase):

    def __init__(self):
        self.name = 'Flickr'
        self.icon = FlickrIcon
        self.exif = FlickrEXIF
        self.info = { 'comments': _('Photo Share Service'),
                      'copyright': 'Copyright © 2009-2010 Yoshizimi Endo',
                      'website': 'http://www.flickr.com/',
                      'authors': ['Yoshizimi Endo'], }

class FlickrPhotoList(base.PhotoList):

    def __init__(self, target, argument, weight, options, photolist):
        super(FlickrPhotoList, self).__init__(
            target, argument, weight, options, photolist)
        self.page_list = FlickrAPIPages(options)
        self.page = 1 # obsolete
        self.photos_other_page = []
        self.argument_group_name = None
  
    def _random_choice(self):
        only_latest = self.options.get('only_latest_roll')

        if only_latest or not self.page_list.pages:
            first_page = True
        else:
            threshold = self._get_threshold()
            rate = random.random()
            first_page = not self.photos_other_page or rate < threshold

        print "pages: %s (%s)" % (self.page_list.pages, self.page_list.perpage),
        print "only_latest: %s, first_page: %s" %  (only_latest, first_page)

        target_list = self.photos if first_page else self.photos_other_page 
        return random.choice(target_list)

    def _get_threshold(self):
        min = self.conf.get_int('plugins/flickr/latest_photos_min_rate', 10)
        original = 100.0 / self.page_list.pages
        threshold = original if original > min else min
        print threshold, min, original

        return threshold / 100.0

    def prepare(self):
        factory = FlickrFactoryAPI()
        api_list = factory.api
        if not self.target in api_list:
            print _("Flickr: %s is invalid target.") % self.target
            return

        self.api = factory.create(self.target, self.argument)
        # print self.api

        if self.api.nsid_conversion:
            nsid_url = self.api.get_url_for_nsid_lookup(self.argument)

            if nsid_url is None:
                print _("Flickr: invalid nsid API url.")
                return
            self._get_url_with_twisted(nsid_url, self._nsid_cb)
        else:
            self._get_url_for(self.argument)

    def _nsid_cb(self, data):
        d = json.loads(data)
        self.nsid_argument, self.argument_group_name = self.api.parse_nsid(d)
        if self.nsid_argument is None:
            print _("flickr: can not find, "), self.argument
            return

        self._get_url_for(self.nsid_argument)

    def _get_url_for(self, argument):
        page = self.page_list.get_page()
        self.page = page # obsolete

        # print page, self.options

        url = self.api.get_url(argument, page)
        if not url: return

        self._get_url_with_twisted(url)
        interval_min = self.api.get_interval()
        self._start_timer(interval_min)

    def _prepare_cb(self, data):
        d = json.loads(data)

        if d.get('stat') == 'fail':
            print _("Flickr API Error (%s): %s") % (d['code'], d['message'])
            return

        self.total = len(d['photos']['photo'])
        self.page_list.update(d['photos'])

        if self.page != self.page_list.page:
            print "oops! page num error."  # obsolete

        if self.page_list.page == 1:
            update_photo_list = self.photos = []
        else:
            update_photo_list = self.photos_other_page = []

        for s in d['photos']['photo']:
            if s['media'] == 'video' or s['server'] is None: continue

            url = "http://farm%s.static.flickr.com/%s/%s_%s.jpg" % (
                s['farm'], s['server'], s['id'], s['secret'])
            nsid = self.nsid_argument if hasattr(self, "nsid_argument") else None
            page_url = self.api.get_page_url(s['owner'], s['id'], nsid)
            argument = self.argument_group_name or self.argument

            try:
                format = '%Y-%m-%d %H:%M:%S'
                date = time.mktime(time.strptime(s['datetaken'], format))
            except (ValueError, OverflowError), info:
                date = s['datetaken']

            data = {'info'       : FlickrPlugin,
                    'target'     : (self.target, argument),
                    'url'        : str(url),
                    'owner_name' : s['ownername'],
                    'owner'      : s['owner'],
                    'id'         : s['id'],
                    'title'      : s['title'],
                    'page_url'   : page_url,
                    'date_taken' : date,
                    # 'description' : s['description']['_content'],
                    'geo'        : {'lon' : s['longitude'],
                                    'lat' : s['latitude']},
                    'trash'      : trash.Ban(self.photolist),
                    'icon'       : FlickrIcon}

            if self.api.get_auth_token():
                state = (self.target == 'Favorites' and not self.argument)
                data['fav'] = FlickrFav(state, {'id': s['id']})

            for type in ['url_z', 'url_l', 'url_o']:
                if s.get(type):
                    url = s.get(type)
                    data[type] = str(url)
                    if type == 'url_o':
                        w, h = int(s.get('width_o')), int(s.get('height_o'))
                        data['size_o'] = [w, h]

            photo = FlickrPhoto(data)
            update_photo_list.append(photo)

class PhotoSourceFlickrUI(ui.PhotoSourceUI):

    def get_options(self):
        return self.options_ui.get_value()

    def _build_target_widget(self):
        super(PhotoSourceFlickrUI, self)._build_target_widget()

        self._widget_cb(self.target_widget)
        self.target_widget.connect('changed', self._widget_cb)

    def _widget_cb(self, widget):
        target = widget.get_active_text()
        api = FlickrFactoryAPI().create(target)

        checkbutton = self.gui.get_object('checkbutton_flickr_id')
        self._change_sensitive_cb(checkbutton, api)

        self.options_ui.checkbutton_flickr_id_sensitive(api)
        self.options_ui.checkbutton_flickr_id.connect(
            'toggled', self._change_sensitive_cb, api)

    def _change_sensitive_cb(self, checkbutton, api):
        default, label = api.set_entry_label()
        check = checkbutton.get_active() if api.is_use_own_id() else default
        self._set_argument_sensitive(label, check)

        # tooltip
        tip = api.tooltip() if check else ""
        self._set_argument_tooltip(tip)

        # ok button sensitive
        arg_entry = self.gui.get_object('entry1')
        state = True if arg_entry.get_text() else not check
        self._set_sensitive_ok_button(arg_entry, state)

    def _label(self):
        keys = FlickrFactoryAPI().api.keys()
        keys.sort()
        label = [api for api in keys]

        if not GConf().get_string('plugins/flickr/nsid'):
            label.remove(_('Your Groups'))

        return label

    def _make_options_ui(self):
        self.options_ui = PhotoSourceOptionsFlickrUI(self.gui, self.data)

class PhotoSourceOptionsFlickrUI(ui.PhotoSourceOptionsUI):

    def get_value(self):
        other_id = self.checkbutton_flickr_id.get_active()
        latest = self.checkbutton_latest.get_active()
        return {'other_id': other_id, 'only_latest_roll': latest}

    def _set_ui(self):
        self.child = self.gui.get_object('flickr_vbox')
        self.checkbutton_flickr_id = self.gui.get_object('checkbutton_flickr_id')
        self.checkbutton_latest = self.gui.get_object('checkbutton_latest_roll')

    def _set_default(self):
        state = True if not self._check_authorized() \
            else self.options.get('other_id', False)
        self.checkbutton_flickr_id.set_active(state)

        latest = self.options.get('only_latest_roll', False)
        self.checkbutton_latest.set_active(latest)

    def checkbutton_flickr_id_sensitive(self, api):
        state = False if not self._check_authorized() else api.is_use_own_id()
        self.checkbutton_flickr_id.set_sensitive(state)

    def _check_authorized(self):
        return GConf().get_string('plugins/flickr/nsid')

class FlickrPhoto(base.Photo):

    def get_image_url(self):
        if self._is_fullscreen_mode():
            w, h = self.get('size_o') or [None, None]
            # print w,h
            cond = w and h and (w <= 1280 or h <= 1024)
            type = 'url_o' if cond else 'url_l'
        else:
            type = 'url'

        url = self.get(type) or self.get('url_z') or self.get('url') 
        # print type, url
        return url

class FlickrFav(object):

    def __init__(self, state=False, arg={}):
        self.fav = state
        self.arg = arg

    def change_fav(self, rate_dummy):
        url = self._get_url()
        self.urlget = UrlGetWithAutoProxy(url)
        d = self.urlget.getPage(url)
        self.fav = not self.fav

    def _get_url(self):
        api = FlickrFavoritesRemoveAPI if self.fav else FlickrFavoritesAddAPI
        url = api().get_url(self.arg['id'])
        return url

class FlickrAPIPages(object):

    def __init__(self, options):
        self.page_list = []
        self.only_latest = options.get('only_latest_roll')

    def get_page(self):
        threshold = 0.2
        random_rate = random.random()

        if self.only_latest or not self.page_list or threshold > random_rate:
            page = 1
        else:
            page = random.sample(self.page_list, 1)[0]

        # print threshold, random_rate, page
        return page

    def update(self, feed):
        self.page = feed.get('page') or 1
        self.pages = feed.get('pages')
        self.perpage = feed.get('perpage')

        if not self.page_list and self.pages:
            self.page_list = range(1, self.pages+1)

        if self.page in self.page_list:
            self.page_list.remove(self.page)

class FlickrEXIF(object):

    def get(self, photo):
        self.photo = photo
        api = FlickrExifAPI()
        url = api.get_url(photo['id'])

        urlget = UrlGetWithAutoProxy(url)
        d = urlget.getPage(url)
        d.addCallback(self._parse_flickr_exif)

        return d

    def _parse_flickr_exif(self, data):
        d = json.loads(data)
        self.photo['exif'] = {'checkd': True}

        if d['stat'] != 'ok':
            print d
            return

        target = {'Make': 'make', 'Model': 'model', 'FNumber': 'fstop', 
                  'ISO': 'iso', 'ExposureTime': 'exposure', 
                  'FocalLength': 'focallength', 'Lens Model': 'lense',
                  'ExposureCompensation' : 'exposurebias', 'Flash': 'flash',}

        for i in [x for x in d['photo']['exif'] if x['tag'] in target.keys()]:
            tag = i['tag']
            raw = i['raw']['_content']

            if tag == 'FocalLength':
                raw = raw.rstrip('m ')
            elif tag == 'ExposureCompensation' and raw == '0':
                continue
            elif tag == 'Flash' and (
                'Off' in raw or 'No' in raw or 'not' in raw):
                continue

            #print i['label'], raw, tag
            self.photo['exif'][ target[tag] ] = raw

class FlickrIcon(WebIconImage):

    def __init__(self):
        self.icon_name = 'flickr.ico'
        self.icon_url = 'http://www.flickr.com/favicon.ico'
