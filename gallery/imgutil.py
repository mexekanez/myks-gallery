# coding: utf-8
# Copyright (c) 2011 Aymeric Augustin. All rights reserved.

from __future__ import division

try:
    from PIL import Image
except ImportError:
    import Image

from django.conf import settings


exif_rotations = (
    None,
    lambda image: image,
    lambda image: image.transpose(Image.FLIP_LEFT_RIGHT),
    lambda image: image.transpose(Image.ROTATE_180),
    # shortcut for image.transpose(Image.ROTATE_180).transpose(FLIP_LEFT_RIGHT)
    lambda image: image.transpose(Image.FLIP_TOP_BOTTOM),
    lambda image: image.transpose(Image.ROTATE_270).transpose(Image.FLIP_LEFT_RIGHT),
    lambda image: image.transpose(Image.ROTATE_270),
    lambda image: image.transpose(Image.ROTATE_90).transpose(Image.FLIP_LEFT_RIGHT),
    lambda image: image.transpose(Image.ROTATE_90),
)


def make_thumbnail(imagepath, thumbpath, size, crop=False):
    image = Image.open(imagepath)
    # Auto-rotate JPEG files based on EXIF information
    if image.format == 'JPEG':
        try:
            # Use of an undocumented API — let's catch exceptions liberally
            orientation = image._getexif()[274]
            image = exif_rotations[orientation](image)
        except Exception, e:
            pass
    # Pre-crop if requested and the aspect ratios don't match exactly
    if crop:
        thumb_width, thumb_height = size
        image_width, image_height = image.size
        if thumb_width * image_height > image_width * thumb_height:
            target_height = image_width * thumb_height // thumb_width
            top = (image_height - target_height) // 2
            image = image.crop((0, top, image_width, top + target_height))
        elif thumb_width * image_height < image_width * thumb_height:
            target_width = image_height * thumb_width // thumb_height
            left = (image_width - target_width) // 2
            image = image.crop((left, 0, left + target_width, image_height))
    # Save the thumbnail
    image.thumbnail(size, Image.ANTIALIAS)
    options = settings.PHOTO_SAVE_OPTIONS.get(image.format, {})
    try:
        image.save(thumbpath, **options)
    except IOError:
        os.unlink(thumbpath)
        raise