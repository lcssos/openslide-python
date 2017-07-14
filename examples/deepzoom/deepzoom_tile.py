#!/usr/bin/env python
#
# deepzoom_tile - Convert whole-slide images to Deep Zoom format
#
# Copyright (c) 2010-2015 Carnegie Mellon University
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of version 2.1 of the GNU Lesser General Public License
# as published by the Free Software Foundation.
#
# This library is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public
# License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this library; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#

"""An example program to generate a Deep Zoom directory tree from a slide."""

from __future__ import print_function
import json
from multiprocessing import Process, JoinableQueue


import openslide
from openslide import open_slide, ImageSlide
# from openslide.deepzoom import DeepZoomGenerator
from optparse import OptionParser
import os
import re
import shutil
import sys
from unicodedata import normalize

from openslide.deepzoom import DeepZoomGenerator
from tile_worker import TileWorker

VIEWER_SLIDE_NAME = 'slide'

from deepzoom_image_tiler import DeepZoomImageTiler


class DeepZoomStaticTiler(object):
    """Handles generation of tiles and metadata for all images in a slide."""

    def __init__(self, slidepath, opts):
        if opts.with_viewer:
            # Check extra dependency before doing a bunch of work
            import jinja2
        self._slide = open_slide(slidepath)
        self._basename = opts.basename
        self._format = opts.format
        self._tile_size = opts.tile_size
        self._overlap = opts.overlap
        self._limit_bounds = opts.limit_bounds
        self._queue = JoinableQueue(2 * opts.workers)
        self._workers = opts.workers
        self._with_viewer = opts.with_viewer
        self._dzi_data = {}
        for _i in range(opts.workers):
            TileWorker(self._queue, slidepath, opts.tile_size, opts.overlap, opts.limit_bounds, opts.quality).start()

    def run(self):
        self._run_image()
        if self._with_viewer:
            for name in self._slide.associated_images:
                self._run_image(name)
            self._write_html()
            self._write_static()
        self._shutdown()

    def _run_image(self, associated=None):
        """Run a single image from self._slide."""
        if associated is None:
            image = self._slide
            if self._with_viewer:
                basename = os.path.join(self._basename, VIEWER_SLIDE_NAME)
            else:
                basename = self._basename
        else:
            image = ImageSlide(self._slide.associated_images[associated])
            basename = os.path.join(self._basename, self._slugify(associated))

        print('start dz')
        dz = DeepZoomGenerator(image, self._tile_size, self._overlap, limit_bounds=self._limit_bounds)

        print('-'*30)
        print('dz.level_count')
        print(dz.level_count)

        print('-' * 30)
        print('dz.level_tiles')
        print(dz.level_tiles)

        print('-' * 30)
        print('dz.level_dimensions')
        print(dz.level_dimensions)

        print('-' * 30)
        print('dz.tile_count')
        print(dz.tile_count)


        tiler = DeepZoomImageTiler(dz, basename, self._format, associated, self._queue)
        tiler.run()

        self._dzi_data[self._url_for(associated)] = tiler.get_dzi()

    def _url_for(self, associated):
        if associated is None:
            base = VIEWER_SLIDE_NAME
        else:
            base = self._slugify(associated)
        return '%s.dzi' % base

    def _write_html(self):
        import jinja2
        env = jinja2.Environment(loader=jinja2.PackageLoader(__name__),
                    autoescape=True)
        template = env.get_template('slide-multipane.html')
        associated_urls = dict((n, self._url_for(n))
                    for n in self._slide.associated_images)
        try:
            mpp_x = self._slide.properties[openslide.PROPERTY_NAME_MPP_X]
            mpp_y = self._slide.properties[openslide.PROPERTY_NAME_MPP_Y]
            mpp = (float(mpp_x) + float(mpp_y)) / 2
        except (KeyError, ValueError):
            mpp = 0
        # Embed the dzi metadata in the HTML to work around Chrome's
        # refusal to allow XmlHttpRequest from file:///, even when
        # the originating page is also a file:///
        data = template.render(slide_url=self._url_for(None),
                    slide_mpp=mpp,
                    associated=associated_urls,
                    properties=self._slide.properties,
                    dzi_data=json.dumps(self._dzi_data))
        with open(os.path.join(self._basename, 'index.html'), 'w') as fh:
            fh.write(data)

    def _write_static(self):
        basesrc = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    'static')
        basedst = os.path.join(self._basename, 'static')
        self._copydir(basesrc, basedst)
        self._copydir(os.path.join(basesrc, 'images'),
                    os.path.join(basedst, 'images'))

    def _copydir(self, src, dest):
        if not os.path.exists(dest):
            os.makedirs(dest)
        for name in os.listdir(src):
            srcpath = os.path.join(src, name)
            if os.path.isfile(srcpath):
                shutil.copy(srcpath, os.path.join(dest, name))

    @classmethod
    def _slugify(cls, text):
        text = normalize('NFKD', text.lower()).encode('ascii', 'ignore').decode()
        return re.sub('[^a-z0-9]+', '_', text)

    def _shutdown(self):
        for _i in range(self._workers):
            self._queue.put(None)
        self._queue.join()


if __name__ == '__main__':
    parser = OptionParser(usage='Usage: %prog [options] <slide>')
    parser.add_option('-B', '--ignore-bounds', dest='limit_bounds',
                default=True, action='store_false',
                help='display entire scan area')
    parser.add_option('-e', '--overlap', metavar='PIXELS', dest='overlap',
                type='int', default=1,
                help='overlap of adjacent tiles [1]')
    parser.add_option('-f', '--format', metavar='{jpeg|png}', dest='format',
                default='jpeg',
                help='image format for tiles [jpeg]')
    parser.add_option('-j', '--jobs', metavar='COUNT', dest='workers',
                type='int', default=4,
                help='number of worker processes to start [4]')
    parser.add_option('-o', '--output', metavar='NAME', dest='basename',
                help='base name of output file')
    parser.add_option('-Q', '--quality', metavar='QUALITY', dest='quality',
                type='int', default=90,
                help='JPEG compression quality [90]')
    parser.add_option('-r', '--viewer', dest='with_viewer',
                action='store_true',default=True,
                help='generate directory tree with HTML viewer')
    parser.add_option('-s', '--size', metavar='PIXELS', dest='tile_size',
                type='int', default=254,
                help='tile size [254]')

    (opts, args) = parser.parse_args()
    try:
        slidepath = args[0]
    except IndexError:
        parser.error('Missing slide argument')
    if opts.basename is None:
        opts.basename = os.path.splitext(os.path.basename(slidepath))[0]

    DeepZoomStaticTiler(slidepath, opts).run()
