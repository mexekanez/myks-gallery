# coding: utf-8
# Copyright (c) 2011-2012 Aymeric Augustin. All rights reserved.

import mimetypes
import os
import random
import stat
import unicodedata

from django.conf import settings
from django.contrib.auth.models import User
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.shortcuts import get_object_or_404
from django.utils.http import http_date
from django.views.generic import ArchiveIndexView, DetailView, YearArchiveView
from django.views.static import was_modified_since

from .models import Album, Photo


class GalleryCommonMixin(object):
    """Provide common methods.

    Also add the value of settings.PHOTO_TITLE in the context as `title`.
    """
    def can_view_all(self):
        if not hasattr(self, '_can_view_all'):
            self._can_view_all = self.request.user.has_perm('gallery.view')
        return self._can_view_all

    def get_context_data(self, **kwargs):
        context = super(GalleryCommonMixin, self).get_context_data(**kwargs)
        context['title'] = getattr(settings, 'PHOTO_TITLE', u"Gallery")
        return context


class AlbumListMixin(object):
    """Perform access control and database optimization for albums."""
    model = Album
    date_field = 'date'

    def get_queryset(self):
        if self.can_view_all():
            qs = Album.objects.all()
            qs = qs.prefetch_related('photo_set')
        else:
            qs = Album.objects.allowed_for_user(self.request.user)
            qs = qs.prefetch_related('access_policy__groups')
            qs = qs.prefetch_related('access_policy__users')
            qs = qs.prefetch_related('photo_set__access_policy__groups')
            qs = qs.prefetch_related('photo_set__access_policy__users')
        return qs


class AlbumListWithPreviewMixin(AlbumListMixin):
    """Compute preview lists for albums."""

    def get_context_data(self, **kwargs):
        context = super(AlbumListWithPreviewMixin, self).get_context_data(**kwargs)
        user = self.request.user
        if not self.can_view_all() and user.is_authenticated():
            # Avoid repeated queries - this is specific to django.contrib.auth
            user = User.objects.prefetch_related('groups').get(pk=user.pk)
        for album in context['object_list']:
            if self.can_view_all():
                photos = album.photo_set.all()
            else:
                photos = []
                for photo in album.photo_set.all():
                    if photo.is_allowed_for_user(user):
                        photos.append(photo)
            album.preview = random.sample(photos, min(len(photos), 5))
        return context


class GalleryIndexView(GalleryCommonMixin, AlbumListWithPreviewMixin, ArchiveIndexView):
    allow_empty = True
    paginate_by = 10


class GalleryYearView(GalleryCommonMixin, AlbumListWithPreviewMixin, YearArchiveView):
    make_object_list = True


class AlbumView(GalleryCommonMixin, AlbumListMixin, DetailView):
    model = Album
    context_object_name = 'album'

    def get_context_data(self, **kwargs):
        context = super(AlbumView, self).get_context_data(**kwargs)
        if self.can_view_all():
            context['photos'] = album.photo_set.all()
        else:
            context['photos'] = album.photo_set.allowed_for_user(self.request.user)
        try:
            context['previous_album'] = self.object.get_previous_in_queryset(self.queryset)
        except Album.DoesNotExist:
            pass
        try:
            context['next_album'] = self.object.get_next_in_queryset(self.queryset)
        except Album.DoesNotExist:
            pass
        return context


class PhotoView(GalleryCommonMixin, DetailView):
    model = Photo
    context_object_name = 'photo'

    def get_queryset(self):
        if self.can_view_all():
            qs = Photo.objects.all()
        else:
            qs = Photo.objects.allowed_for_user(self.request.user)
        return qs.select_related('album')

    def get_context_data(self, **kwargs):
        context = super(PhotoView, self).get_context_data(**kwargs)
        try:
            context['previous_photo'] = self.object.get_previous_in_queryset(self.queryset)
        except Photo.DoesNotExist:
            pass
        try:
            context['next_photo'] = self.object.get_next_in_queryset(self.queryset)
        except Photo.DoesNotExist:
            pass
        return context


def _get_photo_if_allowed(request, pk):
    qs = Photo.objects
    if not request.user.has_perm('gallery.view'):
        qs = qs.allowed_for_user(request.user)
    qs = qs.select_related('album')
    return get_object_or_404(qs, pk=pk)


def resized_photo(request, preset, pk):
    """Serve a resized photo."""
    photo = _get_photo_if_allowed(request, int(pk))
    path = photo.thumbnail(preset)
    response = serve_private_media(request, path)

    root, ext = os.path.splitext(asciify(photo.filename))
    width, height, _ = settings.PHOTO_RESIZE_PRESETS[preset]
    ascii_filename = '%s_%sx%s%s' % (root, width, height, ext)
    response['Content-Disposition'] = 'inline; filename=%s;' % ascii_filename
    return response


def original_photo(request, pk):
    """Serve an original photo."""
    photo = _get_photo_if_allowed(request, int(pk))
    path = photo.abspath()
    response = serve_private_media(request, path)

    ascii_filename = asciify(photo.filename)
    response['Content-Disposition'] = 'inline; filename=%s;' % ascii_filename
    return response


def serve_private_media(request, path):
    """Serve a private media file.

    Here's an example of how to use this function. We want to serve the file
    stored in the 'file' attribute of a 'Document' model only to users who
    have the 'can_download' permission::

        @permission_required('documents.can_download')
        def download_document(request, document_id):
            path = Document.objects.get(pk=document_id).file.path
            return serve_private_media(request, path)

    If ``DEBUG`` is ``True``, this function behaves like Django's static serve
    view. If ``DEBUG`` is ``False``, it sets a header and doesn't send the
    actual contents of the file.

    The name of the header is defined by ``settings.SENDFILE_HEADER``. Use
    ``'X-Accel-Redirect'`` for nginx and ``'X-SendFile'`` for Apache.

    ``path`` must be an absolute path. Depending on your webserver's
    configuration, the header should contain either a relative path or full
    path. Therefore, ``settings.SENDFILE_ROOT`` will be stripped from the
    beginning of the path to create the header's value. It must be the root of
    the internal location under nginx. It may be XSendFilePath or empty for
    Apache.
    """
    if not os.path.exists(path):
        # Don't reveal the file name on the filesystem.
        raise Http404("Requested file doesn't exist.")

    # begin copy-paste from django.views.static.serve
    statobj = os.stat(path)
    mimetype, encoding = mimetypes.guess_type(path)
    mimetype = mimetype or 'application/octet-stream'
    if not was_modified_since(request.META.get('HTTP_IF_MODIFIED_SINCE'),
                              statobj.st_mtime, statobj.st_size):
        return HttpResponseNotModified(mimetype=mimetype)
    # pause copy-paste from django.views.static.serve

    if settings.DEBUG:
        with open(path, 'rb') as f:
            response = HttpResponse(f.read(), mimetype=mimetype)
    else:
        response = HttpResponse('', mimetype=mimetype)
        if settings.SENDFILE_ROOT:
            if not path.startswith(settings.SENDFILE_ROOT):
                raise ValueError("Requested file isn't under SENDFILE_ROOT.")
            path = path[len(settings.SENDFILE_ROOT):]
        response[settings.SENDFILE_HEADER] = path

    # resume copy-paste from django.views.static.serve
    response["Last-Modified"] = http_date(statobj.st_mtime)
    if stat.S_ISREG(statobj.st_mode):
        response["Content-Length"] = statobj.st_size
    if encoding:
        response["Content-Encoding"] = encoding
    # end copy-paste from django.views.static.serve

    return response


def asciify(value):
    return unicodedata.normalize('NFKD', unicode(value)).encode('ascii', 'ignore')
