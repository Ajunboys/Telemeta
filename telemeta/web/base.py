# -*- coding: utf-8 -*-
# Copyright (C) 2007 Samalyse SARL

# This software is a computer program whose purpose is to backup, analyse,
# transcode and stream any audio content with its metadata over a web frontend.

# This software is governed by the CeCILL  license under French law and
# abiding by the rules of distribution of free software.  You can  use,
# modify and/ or redistribute the software under the terms of the CeCILL
# license as circulated by CEA, CNRS and INRIA at the following URL
# "http://www.cecill.info".

# As a counterpart to the access to the source code and  rights to copy,
# modify and redistribute granted by the license, users are provided only
# with a limited warranty  and the software's author,  the holder of the
# economic rights,  and the successive licensors  have only  limited
# liability.

# In this respect, the user's attention is drawn to the risks associated
# with loading,  using,  modifying and/or developing or reproducing the
# software by the user in light of its specific status of free software,
# that may mean  that it is complicated to manipulate,  and  that  also
# therefore means  that it is reserved for developers  and  experienced
# professionals having in-depth computer knowledge. Users are therefore
# encouraged to load and test the software's suitability as regards their
# requirements in conditions enabling the security of their systems and/or
# data to be ensured and,  more generally, to use and operate it in the
# same conditions as regards security.

# The fact that you are presently reading this means that you have had
# knowledge of the CeCILL license and that you accept its terms.

# Author: Olivier Guilyardi <olivier@samalyse.com>

import re
import os
import sys
import csv
import time
import datetime
import timeside

from jsonrpc import jsonrpc_method

from django.utils.decorators import method_decorator
from django.contrib.auth import authenticate, login
from django.template import RequestContext, loader
from django import template
from django.http import HttpResponse, HttpResponseRedirect
from django.http import Http404
from django.shortcuts import render_to_response, redirect
from django.views.generic import list_detail
from django.conf import settings
from django.contrib import auth
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.context_processors import csrf
from django.forms.models import modelformset_factory
from django.contrib.auth.models import User
from django.utils.translation import ugettext
from django.contrib.auth.forms import UserChangeForm

from telemeta.models import *
import telemeta.models
import telemeta.interop.oai as oai
from telemeta.interop.oaidatasource import TelemetaOAIDataSource
from django.core.exceptions import ObjectDoesNotExist
from telemeta.util.unaccent import unaccent
from telemeta.util.unaccent import unaccent_icmp
from telemeta.util.logger import Logger
from telemeta.util.unicode import UnicodeWriter
from telemeta.util.PyRSS2Gen import *
from telemeta.cache import TelemetaCache
import telemeta.web.pages as pages


def render(request, template, data = None, mimetype = None):
    return render_to_response(template, data, context_instance=RequestContext(request), 
                              mimetype=mimetype)

def stream_from_processor(__decoder, __processor):
    while True:
        __frames, eodproc = __processor.process(*__decoder.process())
        if eodproc:
            break
        yield __processor.chunk

def stream_from_file(file):
    chunk_size = 0x10000
    f = open(file, 'r')
    while True:
        __chunk = f.read(chunk_size)
        if not len(__chunk):
            f.close()
            break
        yield __chunk
    
    
class WebView(object):
    """Provide web UI methods"""

    graphers = timeside.core.processors(timeside.api.IGrapher)
    decoders = timeside.core.processors(timeside.api.IDecoder)
    encoders = timeside.core.processors(timeside.api.IEncoder)
    analyzers = timeside.core.processors(timeside.api.IAnalyzer)
    cache_data = TelemetaCache(settings.TELEMETA_DATA_CACHE_DIR)
    cache_export = TelemetaCache(settings.TELEMETA_EXPORT_CACHE_DIR)
    
    def index(self, request):
        """Render the homepage"""
        if not request.user.is_authenticated():
            template = loader.get_template('telemeta/index.html')
            ids = [id for id in MediaItem.objects.all().values_list('id', flat=True).order_by('?')[0:3]]
            items = MediaItem.objects.enriched().filter(pk__in=ids)
            context = RequestContext(request, {
                        'page_content': pages.get_page_content(request, 'parts/home-'+request.LANGUAGE_CODE, ignore_slash_issue=True),
                        'items': items})
            return HttpResponse(template.render(context))
        else:
            template='telemeta/home.html'
            playlists = self.get_playlists(request)
            revisions = self.get_revisions(request)
            searches = Search.objects.filter(username=request.user)
            return render(request, template, {'playlists': playlists, 'searches': searches, 
                                              'revisions': revisions,})
  
    def get_revisions(self, request):
        last_revisions = Revision.objects.all().order_by('-time')[0:15]
        revisions = []
        for revision in last_revisions:
            if revision.element_type == 'item':
                try:
                    element = MediaItem.objects.get(pk=revision.element_id)
                except:
                    element = None
            if revision.element_type == 'collection':
                try:
                    element = MediaCollection.objects.get(pk=revision.element_id)
                except:
                    element = None
            if revision.element_type == 'marker':
                try:
                    element = MediaItemMarker.objects.get(pk=revision.element_id)
                except:
                    element = None
            revisions.append({'revision': revision, 'element': element})
        
        return revisions
        
    def collection_detail(self, request, public_id, template='telemeta/collection_detail.html'):
        collection = MediaCollection.objects.get(public_id=public_id)
        if collection.public_access == 'none' and not request.user.is_staff:
            return HttpResponseRedirect('not_allowed/')
        playlists = self.get_playlists(request)
        translation_list = ['OK', 'Cancel', 'Collection', 'added to playlist']
        translations = {}
        for term in translation_list:
            translations[term] = ugettext(term)
        
        return render(request, template, {'collection': collection, 'playlists' : playlists, 'translations': translations})

    @method_decorator(permission_required('telemeta.change_mediacollection'))
    def collection_edit(self, request, public_id, template='telemeta/collection_edit.html'):
        collection = MediaCollection.objects.get(public_id=public_id)
        if request.method == 'POST':
            form = MediaCollectionForm(data=request.POST, files=request.FILES, instance=collection)
            if form.is_valid():
                code = form.cleaned_data['code']
                if not code:
                    code = public_id
                form.save()
                collection.set_revision(request.user)
                return HttpResponseRedirect('/collections/'+code)
        else:
            form = MediaCollectionForm(instance=collection)
        
        return render(request, template, {'collection': collection, "form": form,})

    @method_decorator(permission_required('telemeta.add_mediacollection'))
    def collection_add(self, request, template='telemeta/collection_add.html'):
        collection = MediaCollection()
        if request.method == 'POST':
            form = MediaCollectionForm(data=request.POST, files=request.FILES, instance=collection)
            if form.is_valid():
                code = form.cleaned_data['code']
                if not code:
                    code = public_id
                form.save()
                collection.set_revision(request.user)
                return HttpResponseRedirect('/collections/'+code)
        else:
            form = MediaCollectionForm(instance=collection)
        
        return render(request, template, {'collection': collection, "form": form,})

    @method_decorator(permission_required('telemeta.add_mediacollection'))
    def collection_copy(self, request, public_id, template='telemeta/collection_edit.html'):
        collection = MediaCollection.objects.get(public_id=public_id)
        new_collection = MediaCollection()
        if request.method == 'POST':
            form = MediaCollectionForm(data=request.POST, files=request.FILES, instance=new_collection)
            if form.is_valid():
                code = form.cleaned_data['code']
                if not code:
                    code = public_id
                form.save()
                new_collection.set_revision(request.user)
                return HttpResponseRedirect('/collections/'+code)
        else:
            form = MediaCollectionForm(instance=collection)
        
        return render(request, template, {'collection': collection, "form": form,})
        
    def item_previous_next(self, item):
        # Get previous and next items
        pks = []
        items = MediaItem.objects.filter(collection=item.collection)
        if len(items) > 1:
            for it in items:
                pks.append(it.pk)
            pks.sort()
            for pk in pks:
                if pk == item.pk:
                    if pk == pks[0]:
                        previous_pk = pks[-1]
                        next_pk = pks[1]
                    elif pk == pks[-1]:
                        previous_pk = pks[-2]
                        next_pk = pks[0]
                    else:
                        previous_pk = pks[pks.index(pk)-1]
                        next_pk = pks[pks.index(pk)+1]
                    for it in items:
                        if it.pk == previous_pk:
                            previous = it
                        if it.pk == next_pk:
                            next = it
                    previous = previous.public_id
                    next = next.public_id
        else:
             previous = item.public_id   
             next = item.public_id
        
        return previous, next
        
    def item_detail(self, request, public_id=None, marker_id=None, template='telemeta/mediaitem_detail.html'):
        """Show the details of a given item"""
        
        if not public_id and marker_id:
            marker = MediaItemMarker.objects.get(public_id=marker_id)
            item_id = marker.item_id
            item = MediaItem.objects.get(id=item_id)
        else:
            item = MediaItem.objects.get(public_id=public_id)
        
        if item.public_access == 'none' and not request.user.is_staff:
            return HttpResponseRedirect('not_allowed/')
            
        # Get TimeSide processors
        formats = []
        for encoder in self.encoders:
            formats.append({'name': encoder.format(), 'extension': encoder.file_extension()})

        graphers = []
        for grapher in self.graphers:
            graphers.append({'name':grapher.name(), 'id': grapher.id()})
        if request.REQUEST.has_key('grapher_id'):
            grapher_id = request.REQUEST['grapher_id']
        else:
            grapher_id = 'waveform'
        
        previous, next = self.item_previous_next(item)
        analyzers = self.item_analyze(item)
        playlists = self.get_playlists(request)
        public_access = self.get_public_access(item.public_access, item.recorded_from_date, item.recorded_to_date)
        
        translation_list = ['OK', 'Cancel', 'Item' 'Marker', 'added to playlist']
        translations = {}
        for term in translation_list:
            translations[term] = ugettext(term)
                
        return render(request, template,
                    {'item': item, 'export_formats': formats,
                    'visualizers': graphers, 'visualizer_id': grapher_id,'analysers': analyzers,
                    'audio_export_enabled': getattr(settings, 'TELEMETA_DOWNLOAD_ENABLED', True),
                    'previous' : previous, 'next' : next, 'marker': marker_id, 'playlists' : playlists, 
                    'public_access': public_access, 'translations': translations, 
                    })
    
    def get_public_access(self, access, date_from, date_to):
        # Rolling publishing date : Public access when time between recorded year 
        # and currant year is over settings value PUBLIC_ACCESS_PERIOD
        if date_to:
            date = date_to
        elif date_from:
            date = date_from
        else:
            date = None
        if access == 'full':
            public_access = True
        else:
            public_access = False
        if date:
            year = str(date).split('-')
            year_now = datetime.datetime.now().strftime("%Y")
            if int(year_now) - int(year[0]) >= settings.TELEMETA_PUBLIC_ACCESS_PERIOD:
                public_access = True
        
        return public_access
        
    @method_decorator(permission_required('telemeta.change_mediaitem'))
    def item_edit(self, request, public_id, template='telemeta/mediaitem_edit.html'):
        """Show the details of a given item"""
        item = MediaItem.objects.get(public_id=public_id)
        
        formats = []
        for encoder in self.encoders:
            formats.append({'name': encoder.format(), 'extension': encoder.file_extension()})

        graphers = []
        for grapher in self.graphers:
            graphers.append({'name':grapher.name(), 'id': grapher.id()})
        if request.REQUEST.has_key('grapher_id'):
            grapher_id = request.REQUEST['grapher_id']
        else:
            grapher_id = 'waveform'
        
        previous, next = self.item_previous_next(item)
        analyzers = self.item_analyze(item)
        
        if request.method == 'POST':
            form = MediaItemForm(data=request.POST, files=request.FILES, instance=item)
            if form.is_valid():
                code = form.cleaned_data['code']
                if not code:
                    code = public_id
                form.save()
                if form.files:
                    self.cache_data.delete_item_data(code)
                    self.cache_export.delete_item_data(code)
                item.set_revision(request.user)
                return HttpResponseRedirect('/items/'+code)
        else:
            form = MediaItemForm(instance=item)
        
        return render(request, template, 
                    {'item': item, 'export_formats': formats, 
                    'visualizers': graphers, 'visualizer_id': grapher_id,'analysers': analyzers,
                    'audio_export_enabled': getattr(settings, 'TELEMETA_DOWNLOAD_ENABLED', True), "form": form, 
                    'previous' : previous, 'next' : next, 
                    })
        
    @method_decorator(permission_required('telemeta.add_mediaitem'))
    def item_add(self, request, template='telemeta/mediaitem_add.html'):
        """Show the details of a given item"""
        item = MediaItem()
        if request.method == 'POST':
            form = MediaItemForm(data=request.POST, files=request.FILES, instance=item)
            if form.is_valid():
                code = form.cleaned_data['code']
                if not code:
                    code = public_id
                form.save()
                item.set_revision(request.user)
                return HttpResponseRedirect('/items/'+code)
        else:
            form = MediaItemForm(instance=item)
        
        return render(request, template, {'item': item, "form": form})
    
    @method_decorator(permission_required('telemeta.add_mediaitem'))
    def item_copy(self, request, public_id, template='telemeta/mediaitem_copy.html'):
        """Show the details of a given item"""
        item = MediaItem.objects.get(public_id=public_id)
        new_item = MediaItem()
        if request.method == 'POST':
            form = MediaItemForm(data=request.POST, files=request.FILES, instance=new_item)
            if form.is_valid():
                code = form.cleaned_data['code']
                if not code:
                    code = public_id
                form.save()
                new_item.set_revision(request.user)
                return HttpResponseRedirect('/items/'+code)
        else:
            form = MediaItemForm(instance=item)
        
        return render(request, template, {'item': item, "form": form})
        
    def item_analyze(self, item):
        public_id = str(item.public_id)
        analyze_file = public_id + '.xml'
        
        if self.cache_data.exists(analyze_file):
            analyzers = self.cache_data.read_analyzer_xml(analyze_file)
            if not item.approx_duration:
                for analyzer in analyzers:
                    if analyzer['id'] == 'duration':
                        value = analyzer['value']
                        time = value.split(':')
                        time[2] = time[2].split('.')[0]
                        time = ':'.join(time)
                        item.approx_duration = str(time)
                        item.save()
        else:     
            analyzers = []
            analyzers_sub = []
            if item.file:
                decoder  = timeside.decoder.FileDecoder(item.file.path)
                pipe = decoder
                for analyzer in self.analyzers:
                    subpipe = analyzer()
                    analyzers_sub.append(subpipe)
                    pipe = pipe | subpipe
                pipe.run()

                mime_type = decoder.format()
                analyzers.append({'name': 'Mime type', 'id': 'mime_type', 'unit': '', 'value': mime_type})
                analyzers.append({'name': 'Channels', 'id': 'channels', 'unit': '', 'value': decoder.channels()})
                
                for analyzer in analyzers_sub:
                    value = analyzer.result()
                    if analyzer.id() == 'duration':
                        approx_value = int(round(value))
                        item.approx_duration = approx_value
                        try:
                            item.save()
                        except:
                            pass
                        value = datetime.timedelta(0,value)
                    
                    analyzers.append({'name':analyzer.name(),
                                      'id':analyzer.id(),
                                      'unit':analyzer.unit(),
                                      'value':str(value)})
                  
                self.cache_data.write_analyzer_xml(analyzers, analyze_file)
            
        return analyzers
    
    def item_analyze_xml(self, request, public_id):
        item = MediaItem.objects.get(public_id=public_id)
        analyze_file = public_id + '.xml'
        if not self.cache_data.exists(analyze_file):
            self.item_analyze(item)
        mime_type = 'text/xml'
        response = HttpResponse(self.cache_data.read_stream_bin(analyze_file), mimetype=mime_type)
        response['Content-Disposition'] = 'attachment; filename='+public_id+'.xml'        
        return response        
        
    def item_visualize(self, request, public_id, visualizer_id, width, height):
        item = MediaItem.objects.get(public_id=public_id)
        mime_type = 'image/png'
        grapher_id = visualizer_id
        
        for grapher in self.graphers:
            if grapher.id() == grapher_id:
                break

        if grapher.id() != grapher_id:
            raise Http404
        
        size = width + '_' + height
        image_file = '.'.join([public_id, grapher_id, size, 'png'])

        if not self.cache_data.exists(image_file):
            if item.file:
                path = self.cache_data.dir + os.sep + image_file
                decoder  = timeside.decoder.FileDecoder(item.file.path)
                graph = grapher(width = int(width), height = int(height))
                pipe = decoder | graph
                pipe.run()
                f = open(path, 'w')
                graph.render(path)
                f.close()
                
        response = HttpResponse(self.cache_data.read_stream_bin(image_file), mimetype=mime_type)
        return response

    def list_export_extensions(self):
        "Return the recognized item export file extensions, as a list"
        list = []
        for encoder in self.encoders:
            list.append(encoder.file_extension())
        return list

    def item_export(self, request, public_id, extension):                    
        """Export a given media item in the specified format (OGG, FLAC, ...)"""
        
        item = MediaItem.objects.get(public_id=public_id)

        public_access = self.get_public_access(item.public_access, item.recorded_from_date, item.recorded_to_date)
        if (not public_access or not settings.TELEMETA_DOWNLOAD_ENABLED) and not request.user.is_staff:
            return HttpResponseRedirect('not_allowed/')

        for encoder in self.encoders:
            if encoder.file_extension() == extension:
                break

        if encoder.file_extension() != extension:
            raise Http404('Unknown export file extension: %s' % extension)

        mime_type = encoder.mime_type()
        file = public_id + '.' + encoder.file_extension()
        audio = item.file.path
        
        analyzers = self.item_analyze(item)
        if analyzers:
            for analyzer in analyzers:
                if analyzer['id'] == 'mime_type':
                    format = analyzer['value']
        else:
            decoder = timeside.decoder.FileDecoder(audio)
            format = decoder.format()
        
        if mime_type in format:
            # source > stream
            response = HttpResponse(stream_from_file(audio), mimetype = mime_type)
            
        else:        
            if not self.cache_export.exists(file):
                decoder = timeside.decoder.FileDecoder(audio)
                # source > encoder > stream
                decoder.setup()
                media = self.cache_export.dir + os.sep + file
                proc = encoder(media, streaming=True)
                proc.setup(channels=decoder.channels(), samplerate=decoder.samplerate())
#                metadata = dublincore.express_item(item).to_list()
#                enc.set_metadata(metadata)
                response = HttpResponse(stream_from_processor(decoder, proc), mimetype = mime_type)
            else:
                # cache > stream
                response = HttpResponse(self.cache_export.read_stream_bin(file), mimetype = mime_type)
        
        response['Content-Disposition'] = 'attachment'
        return response

    def edit_search(self, request, criteria=None):
        year_min, year_max = MediaCollection.objects.all().recording_year_range()
        rec_years = year_min and year_max and range(year_min, year_max + 1) or []
        year_min, year_max = MediaCollection.objects.all().publishing_year_range()
        pub_years = year_min and year_max and range(year_min, year_max + 1) or []
        return render(request, 'telemeta/search_criteria.html', {
            'rec_years': rec_years,
            'pub_years': pub_years,
            'ethnic_groups': MediaItem.objects.all().ethnic_groups(),
            'criteria': criteria
        })

    def complete_location(self, request, with_items=True):
        input = request.REQUEST
       
        token = input['q']
        limit = int(input['limit'])
        if with_items:
            locations = MediaItem.objects.all().locations()
        else:
            locations = Location.objects.all()

        locations = locations.filter(name__istartswith=token).order_by('name')[:limit]
        data = [unicode(l) + " (%d items)" % l.items().count() for l in locations]

        return HttpResponse("\n".join(data))

    def search(self, request, type = None):
        """Perform a search through collections and items metadata"""
        collections = MediaCollection.objects.enriched()
        items = MediaItem.objects.enriched()
        input = request.REQUEST
        criteria = {}

        switch = {
            'pattern': lambda value: ( 
                collections.quick_search(value), 
                items.quick_search(value)),
            'title': lambda value: (
                collections.word_search('title', value), 
                items.by_title(value)),
            'location': lambda value: (
                collections.by_location(Location.objects.get(name=value)), 
                items.by_location(Location.objects.get(name=value))),
            'continent': lambda value: (
                collections.by_continent(value), 
                items.filter(continent = value)),
            'ethnic_group': lambda value: (
                collections.by_ethnic_group(value), 
                items.filter(ethnic_group = value),
                EthnicGroup.objects.get(pk=value)),
            'creator': lambda value: (
                collections.word_search('creator', value),
                items.word_search('collection__creator', value)),
            'collector': lambda value: (
                collections.by_fuzzy_collector(value),
                items.by_fuzzy_collector(value)),
            'rec_year_from': lambda value: (
                collections.by_recording_year(int(value), int(input.get('rec_year_to', value))), 
                items.by_recording_date(datetime.date(int(value), 1, 1), 
                                        datetime.date(int(input.get('rec_year_to', value)), 12, 31))),
            'rec_year_to': lambda value: (collections, items),
            'pub_year_from': lambda value: (
                collections.by_publish_year(int(value), int(input.get('pub_year_to', value))), 
                items.by_publish_year(int(value), int(input.get('pub_year_to', value)))),
            'pub_year_to': lambda value: (collections, items),
        }
       
        for key, value in input.items():
            func = switch.get(key)
            if func and value and value != "0":
                try:
                    res = func(value)
                    if len(res)  > 2:
                        collections, items, value = res
                    else: 
                        collections, items = res
                except ObjectDoesNotExist:
                    collections = collections.none()
                    items = items.none()

                criteria[key] = value

        if type is None:
            if collections.count():
                type = 'collections'
            else:
                type = 'items'

        if type == 'items':
            objects = items
        else:
            objects = collections

        return list_detail.object_list(request, objects, 
            template_name='telemeta/search_results.html', paginate_by=20,
            extra_context={'criteria': criteria, 'collections_num': collections.count(), 
                'items_num': items.count(), 'type' : type})

    # ADMIN
    @method_decorator(permission_required('sites.change_site'))
    def admin_index(self, request):
        return render(request, 'telemeta/admin.html', self.__get_admin_context_vars())

    @method_decorator(permission_required('sites.change_site'))
    def admin_general(self, request):
        return render(request, 'telemeta/admin_general.html', self.__get_admin_context_vars())
    
    @method_decorator(permission_required('sites.change_site'))
    def admin_enumerations(self, request):
        return render(request, 'telemeta/admin_enumerations.html', self.__get_admin_context_vars())
    @method_decorator(permission_required('sites.change_site'))
    def admin_users(self, request):
        users = User.objects.all()
        return render(request, 'telemeta/admin_users.html', {'users': users})

    # ENUMERATIONS
    def __get_enumerations_list(self):
        from django.db.models import get_models
        models = get_models(telemeta.models)

        enumerations = []
        for model in models:
            if issubclass(model, Enumeration):
                enumerations.append({"name": model._meta.verbose_name, 
                    "id": model._meta.module_name})

        cmp = lambda obj1, obj2: unaccent_icmp(obj1['name'], obj2['name'])
        enumerations.sort(cmp)
        return enumerations                    
    
    def __get_admin_context_vars(self):
        return {"enumerations": self.__get_enumerations_list()}
    
    def __get_enumeration(self, id):
        from django.db.models import get_models
        models = get_models(telemeta.models)
        for model in models:
            if model._meta.module_name == id:
                break

        if model._meta.module_name != id:
            return None

        return model

    @method_decorator(permission_required('telemeta.change_keyword'))
    def edit_enumeration(self, request, enumeration_id):        

        enumeration  = self.__get_enumeration(enumeration_id)
        if enumeration == None:
            raise Http404

        vars = self.__get_admin_context_vars()
        vars["enumeration_id"] = enumeration._meta.module_name
        vars["enumeration_name"] = enumeration._meta.verbose_name            
        vars["enumeration_values"] = enumeration.objects.all()
        return render(request, 'telemeta/enumeration_edit.html', vars)

    @method_decorator(permission_required('telemeta.add_keyword'))
    def add_to_enumeration(self, request, enumeration_id):        

        enumeration  = self.__get_enumeration(enumeration_id)
        if enumeration == None:
            raise Http404

        enumeration_value = enumeration(value=request.POST['value'])
        enumeration_value.save()

        return self.edit_enumeration(request, enumeration_id)

    @method_decorator(permission_required('telemeta.change_keyword'))
    def update_enumeration(self, request, enumeration_id):        
        
        enumeration  = self.__get_enumeration(enumeration_id)
        if enumeration == None:
            raise Http404
        
        if request.method == 'POST':
            enumeration.objects.filter(id__in=request.POST.getlist('sel')).delete()

        return self.edit_enumeration(request, enumeration_id)

    @method_decorator(permission_required('telemeta.change_keyword'))
    def edit_enumeration_value(self, request, enumeration_id, value_id):        

        enumeration  = self.__get_enumeration(enumeration_id)
        if enumeration == None:
            raise Http404
        
        vars = self.__get_admin_context_vars()
        vars["enumeration_id"] = enumeration._meta.module_name
        vars["enumeration_name"] = enumeration._meta.verbose_name            
        vars["enumeration_record"] = enumeration.objects.get(id__exact=value_id)
        return render(request, 'telemeta/enumeration_edit_value.html', vars)

    @method_decorator(permission_required('telemeta.change_keyword'))
    def update_enumeration_value(self, request, enumeration_id, value_id):        

        if request.method == 'POST':
            enumeration  = self.__get_enumeration(enumeration_id)
            if enumeration == None:
                raise Http404
       
            record = enumeration.objects.get(id__exact=value_id)
            record.value = request.POST["value"]
            record.save()

        return self.edit_enumeration(request, enumeration_id)
  

    # INSTRUMENTS
    @method_decorator(permission_required('telemeta.change_instrument'))
    def edit_instrument(self, request):        
        
        instruments = Instrument.objects.all().order_by('name')
        if instruments == None:
            raise Http404
        return render(request, 'telemeta/instrument_edit.html', {'instruments': instruments})

    @method_decorator(permission_required('telemeta.add_instrument'))
    def add_to_instrument(self, request):        

        if request.method == 'POST':
            instrument = Instrument(name=request.POST['value'])
            instrument.save()

        return self.edit_instrument(request)

    @method_decorator(permission_required('telemeta.change_instrument'))
    def update_instrument(self, request):        
        
        if request.method == 'POST':
            Instrument.objects.filter(id__in=request.POST.getlist('sel')).delete()

        return self.edit_instrument(request)

    @method_decorator(permission_required('telemeta.change_instrument'))
    def edit_instrument_value(self, request, value_id):        
        instrument = Instrument.objects.get(id__exact=value_id)
        
        return render(request, 'telemeta/instrument_edit_value.html', {'instrument': instrument})

    @method_decorator(permission_required('telemeta.change_instrument'))
    def update_instrument_value(self, request, value_id):        

        if request.method == 'POST':       
            instrument = Instrument.objects.get(id__exact=value_id)
            instrument.name = request.POST["value"]
            instrument.save()

        return self.edit_instrument(request)
        
    def collection_playlist(self, request, public_id, template, mimetype):
        try:
            collection = MediaCollection.objects.get(public_id=public_id)
        except ObjectDoesNotExist:
            raise Http404

        template = loader.get_template(template)
        context = RequestContext(request, {'collection': collection, 'host': request.META['HTTP_HOST']})
        return HttpResponse(template.render(context), mimetype=mimetype)

    def item_playlist(self, request, public_id, template, mimetype):
        try:
            item = MediaItem.objects.get(public_id=public_id)
        except ObjectDoesNotExist:
            raise Http404

        template = loader.get_template(template)
        context = RequestContext(request, {'item': item, 'host': request.META['HTTP_HOST']})
        return HttpResponse(template.render(context), mimetype=mimetype)

    def list_continents(self, request):
        continents = MediaItem.objects.all().countries(group_by_continent=True)
        return render(request, 'telemeta/geo_continents.html', 
                    {'continents': continents, 'gmap_key': settings.TELEMETA_GMAP_KEY })

    def country_info(self, request, id):
        country = Location.objects.get(pk=id)
        return render(request, 'telemeta/country_info.html', {
            'country': country, 'continent': country.continents()[0]})

    def list_countries(self, request, continent):                    
        continent = Location.objects.by_flatname(continent)[0]
        countries = MediaItem.objects.by_location(continent).countries()

        return render(request, 'telemeta/geo_countries.html', {
            'continent': continent,
            'countries': countries
        })

    def list_country_collections(self, request, continent, country):
        continent = Location.objects.by_flatname(continent)[0]
        country = Location.objects.by_flatname(country)[0]
        objects = MediaCollection.objects.enriched().by_location(country)
        return list_detail.object_list(request, objects, 
            template_name='telemeta/geo_country_collections.html', paginate_by=20,
            extra_context={'country': country, 'continent': continent})

    def list_country_items(self, request, continent, country):
        continent = Location.objects.by_flatname(continent)[0]
        country = Location.objects.by_flatname(country)[0]
        objects = MediaItem.objects.enriched().by_location(country)
        return list_detail.object_list(request, objects, 
            template_name='telemeta/geo_country_items.html', paginate_by=20,
            extra_context={'country': country, 'continent': continent})

    def handle_oai_request(self, request):
        url         = 'http://' + request.META['HTTP_HOST'] + request.path
        datasource  = TelemetaOAIDataSource()
        admin       = settings.ADMINS[0][1]
        provider    = oai.DataProvider(datasource, "Telemeta", url, admin)
        args        = request.GET.copy()
        args.update(request.POST)
        return HttpResponse(provider.handle(args), mimetype='text/xml')
        
    def render_flatpage(self, request, path):
        try:
            content = pages.get_page_content(request, path)
        except pages.MalformedPagePath:
            return redirect(request.path + '/')

        if isinstance(content, pages.PageAttachment):
            return HttpResponse(content, content.mimetype())
        else:
            return render(request, 'telemeta/flatpage.html', {'page_content': content })

    def logout(self, request):
        auth.logout(request)
        return redirect('telemeta-home')

    #MARKERS
    @jsonrpc_method('telemeta.add_marker')
    def add_marker(request, marker):
        # marker must be a dict
        if isinstance(marker, dict):
            item_id = marker['item_id']
            item = MediaItem.objects.get(code=item_id)
            m = MediaItemMarker(item=item) 
            m.public_id = marker['public_id']
            m.time = float(marker['time'])
            m.title = marker['title']
            m.description = marker['description']
            m.author = User.objects.get(username=marker['author'])
            m.save()
            m.set_revision(request.user)
        else:
            raise 'Error : Bad marker dictionnary'

    @jsonrpc_method('telemeta.del_marker')
    def del_marker(request, public_id):
        m = MediaItemMarker.objects.get(public_id=public_id)
        m.delete()
        
    @jsonrpc_method('telemeta.get_markers')
    def get_markers(request, item_id):
        item = MediaItem.objects.get(public_id=item_id)
        markers = MediaItemMarker.objects.filter(item=item.pk)
        list = []
        for marker in markers:
            dict = {}
            dict['public_id'] = marker.public_id
            dict['time'] = str(marker.time)
            dict['title'] = marker.title
            dict['description'] = marker.description
            dict['author'] = marker.author.username
            list.append(dict)
        return list

    @jsonrpc_method('telemeta.update_marker')
    def update_marker(request, marker):
        if isinstance(marker, dict):
            m = MediaItemMarker.objects.get(public_id=marker['public_id'])
            m.time = float(marker['time'])
            m.title = marker['title']
            m.description = marker['description']
            m.save()
            m.set_revision(request.user)
        else:
            raise 'Error : Bad marker dictionnary'
 
    # PLAYLISTS
    @jsonrpc_method('telemeta.add_playlist')
    def add_playlist(request, playlist):
        # playlist must be a dict
        if isinstance(playlist, dict):
            m = Playlist()
            m.public_id = playlist['public_id']
            m.title = playlist['title']
            m.description = playlist['description']
            m.author = request.user
            m.save()
        else:
            raise 'Error : Bad playlist dictionnary'

    @jsonrpc_method('telemeta.del_playlist')
    def del_playlist(request, public_id):
        m = Playlist.objects.get(public_id=public_id)
        m.delete()
        
    def get_playlists(self, request, user=None):
        if not user:
            user = request.user
        playlists = []
        if user.is_authenticated():
            user_playlists = Playlist.objects.filter(author=user)
            for playlist in user_playlists:
                playlist_resources = PlaylistResource.objects.filter(playlist=playlist)
                resources = []
                for resource in playlist_resources:
                    try:
                        if resource.resource_type == 'item':
                            element = MediaItem.objects.get(public_id=resource.resource_id)
                        if resource.resource_type == 'collection':
                            element = MediaCollection.objects.get(public_id=resource.resource_id)
                        if resource.resource_type == 'marker':
                            element = MediaItemMarker.objects.get(public_id=resource.resource_id)
                    except:
                        element = None
                    resources.append({'element': element, 'type': resource.resource_type, 'public_id': resource.public_id })
                playlists.append({'playlist': playlist, 'resources': resources})
        return playlists
        
    @jsonrpc_method('telemeta.update_playlist')
    def update_playlist(request, playlist):
        if isinstance(playlist, dict):
            m = Playlist.objects.get(public_id=playlist['public_id'])
            m.title = float(playlist['title'])
            m.description = playlist['description']
            m.save()
        else:
            raise 'Error : Bad playlist dictionnary'
 
    @jsonrpc_method('telemeta.add_playlist_resource')
    def add_playlist_resource(request, playlist_id, playlist_resource):
        # playlist_resource must be a dict
        if isinstance(playlist_resource, dict):
            m = PlaylistResource()
            m.public_id = playlist_resource['public_id']
            m.playlist = Playlist.objects.get(public_id=playlist_id, author=request.user)
            m.resource_type = playlist_resource['resource_type']
            m.resource_id = playlist_resource['resource_id']
            m.save()
        else:
            raise 'Error : Bad playlist_resource dictionnary'

    @jsonrpc_method('telemeta.del_playlist_resource')
    def del_playlist_resource(request, public_id):
        m = PlaylistResource.objects.get(public_id=public_id)
        m.delete()
        

    def playlist_csv_export(self, request, public_id, resource_type):
        playlist = Playlist.objects.get(public_id=public_id, author=request.user)
        resources = PlaylistResource.objects.filter(playlist=playlist)
        response = HttpResponse(mimetype='text/csv')
        response['Content-Disposition'] = 'attachment; filename='+playlist.title+'_'+resource_type+'.csv'
        writer = UnicodeWriter(response)
        
        elements = []
        for resource in resources:
            if resource_type == 'items':
                if resource.resource_type == 'collection':
                    collection = MediaCollection.objects.get(code=resource.resource_id)
                    collection_items = MediaItem.objects.filter(collection=collection)
                    for item in collection_items:
                        elements.append(item)
                elif resource.resource_type == 'item':
                    item = MediaItem.objects.get(code=resource.resource_id)
                    elements.append(item)
                
            elif resource_type == 'collections':
                if resource.resource_type == 'collection':
                    collection = MediaCollection.objects.get(code=resource.resource_id)
                    elements.append(collection)
                
        if elements:
            element = elements[0].to_dict()
            tags = element.keys()
            writer.writerow(tags)
            
            for element in elements:
                data = []
                element = element.to_dict()
                for tag in tags:
                    data.append(element[tag])
                writer.writerow(data)
        return response
        
    def rss(self, request):
        "Render the RSS feed of last revisions"
        rss_item_list = []
        organization = settings.TELEMETA_ORGANIZATION
        subjects = settings.TELEMETA_SUBJECTS
        rss_host = request.META['HTTP_HOST']
        date_now = datetime.datetime.now()
        revisions = self.get_revisions(request)
        tags = ['title', 'description', 'comment']
        
        for r in revisions:
            revision = r['revision']
            element = r['element']
            if element:
                link = 'http://' + rss_host + '/' + revision.element_type + 's/' + str(element.public_id)                
                description = ''
                dict = element.to_dict()
                for tag in dict.keys():
                    try:
                        value = dict[tag]
                        if value != '':
                            description += tag + ' : ' + value + '<br />'
                    except:
                        continue
                    if tag == 'title':
                        if element.title == '':
                            title = str(element.public_id)
                        else:
                            title = element.title
                        
                rss_item_list.append(RSSItem(
                        title = title,
                        link = link,
                        description = description.encode('utf-8'),
                        guid = Guid(link),
                        pubDate = revision.time,)
                        )
                        
        rss = RSS2(title = organization + ' - Telemeta - last changes',
                            link = rss_host,
                            description = ' '.join([subject.decode('utf-8') for subject in subjects]),
                            lastBuildDate = str(date_now),
                            items = rss_item_list,)
        
        feed = rss.to_xml(encoding='utf-8')
        response = HttpResponse(feed, mimetype='application/rss+xml')
        return response
        
    def not_allowed(self, request,  public_id = None):
        mess = ugettext('Access not allowed') 
        title = public_id + ' : ' + mess
        description = 'Please login or contact the website administator to get admin or private access.'
        messages.error(request, title)
        return render(request, 'telemeta/messages.html', {'description' : description})
    
    @method_decorator(login_required)
    def profile_detail(self, request, username, template='telemeta/profile_detail.html'):
        user = User.objects.get(username=username)
        try:
            profile = user.get_profile()
        except:
            profile = None
        playlists = self.get_playlists(request, user)
        return render(request, template, {'profile' : profile, 'usr': user, 'playlists': playlists})
        
    def profile_edit(self, request, username, template='telemeta/profile_edit.html'):
        if request.user.is_staff:
            user_hidden_fields = ['profile-user', 'user-password', 'user-last_login', 'user-date_joined']
        else:
            user_hidden_fields = ['user-username', 'user-is_staff', 'profile-user', 'user-is_active', 
                         'user-password', 'user-last_login', 'user-date_joined', 'user-groups', 
                         'user-user_permissions', 'user-is_superuser', 'profile-expiration_date']
        
        user = User.objects.get(username=username)
        if user != request.user and not request.user.is_staff:
            return HttpResponseRedirect('/accounts/'+username+'/not_allowed/')
        
        try:
            profile = user.get_profile()
        except:
            profile = UserProfile(user=user)
            
        if request.method == 'POST':
            user_form = UserChangeForm(request.POST, instance=user, prefix='user')
            profile_form = UserProfileForm(request.POST, instance=profile, prefix='profile')
            if user_form.is_valid() and profile_form.is_valid():
                user_form.save()
                profile_form.save()
                return HttpResponseRedirect('/accounts/'+username+'/profile/')
        else:
            user_form = UserChangeForm(instance=user, prefix='user')
            profile_form = UserProfileForm(instance=profile, prefix='profile')
            forms = [user_form, profile_form]
        return render(request, template, {'forms': forms, 'usr': user, 'user_hidden_fields': user_hidden_fields})
        
