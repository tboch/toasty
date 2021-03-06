"""
Generate PNG tile directories
"""
from __future__ import print_function, division
import os
import logging

import numpy as np

from ._libtoasty import subsample, mid
from .io import save_png
from .norm import normalize
from collections import defaultdict, namedtuple

level1 = [[np.radians(c) for c in row]
          for row in [[(0, -90), (90, 0), (0, 90), (180, 0)],
                      [(90, 0), (0, -90), (0, 0), (0, 90)],
                      [(0, 90), (0, 0), (0, -90), (270, 0)],
                      [(180, 0), (0, 90), (270, 0), (0, -90)]]
          ]

Pos = namedtuple('Pos', 'n x y')
Tile = namedtuple('Tile', 'pos increasing corners')


def _postfix_corner(tile, depth, bottom_only):
    """
    Yield subtiles of a given tile, in postfix order


    Parameters
    ----------
    tile : (Pos, corner, increasing)
      Description of Current tile
    depth : int
      Depth to descend to
    bottom_only : bool
      If True, only yield tiles at max_depth
    """
    n = tile[0].n
    if n > depth:
        return

    for child in _div4(*tile):
        for item in _postfix_corner(child, depth, bottom_only):
            yield item

    if n == depth or not bottom_only:
        yield tile


def _div4(pos, c, increasing):
    n, x, y = pos.n, pos.x, pos.y
    ul, ur, lr, ll = c
    to = mid(ul, ur)
    ri = mid(ur, lr)
    bo = mid(lr, ll)
    le = mid(ll, ul)
    ce = mid(ll, ur) if increasing else mid(ul, lr)

    return [(Pos(n=n + 1, x=2 * x, y=2 * y), (ul, to, ce, le), increasing),
            (Pos(n=n + 1, x=2 * x + 1, y=2 * y), (to, ur, ri, ce), increasing),
            (Pos(n=n + 1, x=2 * x, y=2 * y + 1), (le, ce, bo, ll), increasing),
            (Pos(n=n + 1, x=2 * x + 1, y=2 * y + 1), (ce, ri, lr, bo),
             increasing)]


def _parent(child):
    """
    Given a toast tile, return the address of the parent,
    as well as the corner of the parent that this tile occupies

    Returns
    -------
    Pos, xcorner, ycorner
    """
    parent = Pos(n=child.n - 1, x=child.x // 2, y=child.y // 2)
    left = child.x % 2
    top = child.y % 2
    return (parent, left, top)


def iter_corners(depth, bottom_only=True):
    """
    Iterate over toast tiles and return the corners.
    Tiles are traversed in post-order (children before parent)

    Parameters
    ----------
    depth : int
      The tile depth to recurse to

    bottom_only : bool
      If True, then only the lowest tiles will be yielded

    Yields
    ------
    pos, corner
    """
    todo = [(Pos(n=1, x=0, y=0), level1[0], True),
            (Pos(n=1, x=1, y=0), level1[1], False),
            (Pos(n=1, x=1, y=1), level1[2], True),
            (Pos(n=1, x=0, y=1), level1[3], False)]

    for t in todo:
        for item in _postfix_corner(t, depth, bottom_only):
            yield item


def iter_tiles(data_sampler, depth, merge=True):
    """
    Create a hierarchy of toast tiles

    Parameters
    ----------
    data_sampler : function
       A function that takes two 2D numpy arrays of (lon, lat) as input,
       and returns an image of the original dataset sampled
       at these locations

    depth : int
      The maximum depth to tile to. A depth of N creates
      4^N pngs at the deepest level

    merge : bool or callable (default True)
      How to treat lower resolution tiles.
      - If True, tiles above the lowest level (highest resolution)
        will be computed by averaging and downsampling the 4 subtiles.
      - If False, sampler will be called explicitly for all tiles
      - If a callable object, this object will be passed the
        4x oversampled image to downsample

    Yields
    ------
    (pth, tile) : str, ndarray
      pth is the relative path where the tile image should be saved
    """
    if merge is True:
        merge = _default_merge

    parents = defaultdict(dict)

    for node, c, increasing in iter_corners(max(depth, 1),
                                            bottom_only=merge):

        l, b = subsample(c[0], c[1], c[2], c[3], 256, increasing)
        img = data_sampler(l, b)

        for pth, img in _trickle_up(img, node, parents, merge, depth):
            yield pth, img


def _trickle_up(im, node, parents, merge, depth):
    """
    When a new toast tile is ready, propagate it up the hierarchy
    and recursively yield its completed parents
    """

    n, x, y = node.n, node.x, node.y

    pth = os.path.join('%i' % n, '%i' % y, '%i_%i.png' % (y, x))

    nparent = sum(len(v) for v in parents.values())
    assert nparent <= 4 * max(depth, 1)

    if depth >= n:  # handle special case of depth=0, n=1
        yield pth, im

    if n == 0:
        return

    # - If not merging and not at level 1, no need to accumulate
    if not merge and n > 1:
        return

    parent, xc, yc = _parent(node)
    corners = parents[parent]
    corners[(xc, yc)] = im

    if len(corners) < 4:  # parent not yet ready
        return

    parents.pop(parent)
    ul = corners[(0, 0)]
    ur = corners[(1, 0)]
    bl = corners[(0, 1)]
    br = corners[(1, 1)]
    mosaic = np.vstack((np.hstack((ul, ur)), np.hstack((bl, br))))
    im = (merge or _default_merge)(mosaic)

    for item in _trickle_up(im, parent, parents, merge, depth):
        yield item


def _default_merge(mosaic):
    """The default merge strategy -- just average all 4 pixels"""
    return (mosaic[::2, ::2] / 4. +
            mosaic[1::2, ::2] / 4. +
            mosaic[::2, 1::2] / 4. +
            mosaic[1::2, 1::2] / 4.).astype(mosaic.dtype)


def gen_wtml(base_dir, depth, **kwargs):
    """
    Create a minimal WTML record for a pyramid generated by toasty

    Parameters
    ----------
    base_dir : str
      The base path to a toast pyramid, as you wish for it to appear
      in the WTML file (i.e., this should be a path visible to a server)
    depth : int
      The maximum depth of the pyramid

    Optional Keywords
    -----------------
    FolderName
    BandPass
    Name
    Credits
    CreditsUrl
    ThumbnailUrl

    Returns
    -------
    wtml : str
      A WTML record
    """
    kwargs.setdefault('FolderName', 'Toasty')
    kwargs.setdefault('BandPass', 'Visible')
    kwargs.setdefault('Name', 'Toasty map')
    kwargs.setdefault('Credits', 'Toasty')
    kwargs.setdefault('CreditsUrl', 'http://github.com/ChrisBeaumont/toasty')
    kwargs.setdefault('ThumbnailUrl', '')
    kwargs['url'] = base_dir
    kwargs['depth'] = depth

    template = ('<Folder Name="{FolderName}">\n'
                '<ImageSet Generic="False" DataSetType="Sky" '
                'BandPass="{BandPass}" Name="{Name}" '
                'Url="{url}/{{1}}/{{3}}/{{3}}_{{2}}.png" BaseTileLevel="0" '
                'TileLevels="{depth}" BaseDegreesPerTile="180" '
                'FileType=".png" BottomsUp="False" Projection="Toast" '
                'QuadTreeMap="" CenterX="0" CenterY="0" OffsetX="0" '
                'OffsetY="0" Rotation="0" Sparse="False" '
                'ElevationModel="False">\n'
                '<Credits> {Credits} </Credits>\n'
                '<CreditsUrl>{CreditsUrl}</CreditsUrl>\n'
                '<ThumbnailUrl>{ThumbnailUrl}</ThumbnailUrl>\n'
                '<Description/>\n</ImageSet>\n</Folder>')
    return template.format(**kwargs)


def toast(data_sampler, depth, base_dir, wtml_file=None, merge=True):
    """
    Build a directory of toast tiles

    Parameters
    ----------
    data_sampler : func
      A function of (lon, lat) that samples a dataset
      at the input 2D coordinate arrays
    depth : int
      The maximum depth to generate tiles for.
      4^n tiles are generated at each depth n
    base_dir : str
      The path to create the files at
    wtml_file : str (optional)
      The path to write a WTML file to. If not present,
      no file will be written
    merge : bool or callable (default True)
      How to treat lower resolution tiles.
      - If True, tiles above the lowest level (highest resolution)
      will be computed by averaging and downsampling the 4 subtiles.
      - If False, sampler will be called explicitly for all tiles
      - If a callable object, this object will be passed the
        4x oversampled image to downsample
    """
    if wtml_file is not None:
        wtml = gen_wtml(base_dir, depth)
        with open(wtml_file, 'w') as outfile:
            outfile.write(wtml)

    num = 0
    for pth, tile in iter_tiles(data_sampler, depth, merge):
        num += 1
        if num % 10 == 0:
            logging.getLogger(__name__).info("Finished %i of %i tiles" %
                                             (num, depth2tiles(depth)))
        pth = os.path.join(base_dir, pth)
        direc, _ = os.path.split(pth)
        if not os.path.exists(direc):
            os.makedirs(direc)
        save_png(pth, tile)


def depth2tiles(depth):
    return (4 ** (depth + 1) - 1) // 3


def _find_extension(pth):
    """
    Find the first HEALPIX extension in a fits file,
    and return the extension number. Else, raise an IndexError
    """
    for i, hdu in enumerate(pth):
        if hdu.header.get('PIXTYPE') == 'HEALPIX':
            return i
    else:
        raise IndexError("No HEALPIX extensions found in %s" % pth.filename())


def _guess_healpix(pth, extension=None):
    # try to guess healpix_sampler arguments from
    # a file

    from astropy.io import fits
    f = fits.open(pth)

    if extension is None:
        extension = _find_extension(f)

    data, hdr = f[extension].data, f[extension].header
    # grab the first healpix parameter
    data = data[data.dtype.names[0]]

    nest = hdr.get('ORDERING') == 'NESTED'
    coord = hdr.get('COORDSYS', 'C')

    return data, nest, coord


def healpix_sampler(data, nest=False, coord='C', interpolation='nearest'):
    """
    Build a sampler for Healpix images

    Parameters
    ----------
    data : array
      The healpix data
    nest : bool (default: False)
      Whether the data is ordered in the nested healpix style
    coord : 'C' | 'G'
      Whether the image is in Celestial (C) or Galactic (G) coordinates
    interpolation : 'nearest' | 'bilinear'
      What interpolation scheme to use.

      WARNING: bilinear uses healpy's get_interp_val,
               which seems prone to segfaults

    Returns
    -------
    A function which samples the healpix image, given arrays
    of (lon, lat)
    """
    from healpy import ang2pix, get_interp_val, npix2nside
    from astropy.coordinates import Galactic, FK5
    import astropy.units as u

    interp_opts = ['nearest', 'bilinear']
    if interpolation not in interp_opts:
        raise ValueError("Invalid interpolation %s. Must be one of %s" %
                         (interpolation, interp_opts))
    if coord.upper() not in 'CG':
        raise ValueError("Invalid coord %s. Must be 'C' or 'G'" % coord)

    galactic = coord.upper() == 'G'
    interp = interpolation == 'bilinear'
    nside = npix2nside(data.size)

    def vec2pix(l, b):
        if galactic:
            f = FK5(l, b, unit=(u.rad, u.rad))
            g = f.transform_to(Galactic)
            l, b = g.l.rad, g.b.rad

        theta = np.pi / 2 - b
        phi = l

        if interp:
            return get_interp_val(data, theta, phi, nest=nest)

        return data[ang2pix(nside, theta, phi, nest=nest)]

    return vec2pix


def cartesian_sampler(data):
    """Return a sampler function for a dataset in the cartesian projection

    The image is assumed to be oriented with longitude increasing to the left,
    with (l,b) = (0,0) at the center pixel

    Parameters
    ----------
    data : array-like
      The map to sample
    """
    data = np.asarray(data)
    ny, nx = data.shape[0:2]

    if ny * 2 != nx:
        raise ValueError("Map must be twice as wide as it is tall")

    def vec2pix(l, b):
        l = (l + np.pi) % (2 * np.pi)
        l[l < 0] += 2 * np.pi
        l = nx * (1 - l / (2 * np.pi))
        l = np.clip(l.astype(np.int), 0, nx - 1)
        b = ny * (1 - (b + np.pi / 2) / np.pi)
        b = np.clip(b.astype(np.int), 0, ny - 1)
        return data[b, l]

    return vec2pix


def normalizer(sampler, vmin, vmax, scaling='linear',
               bias=0.5, contrast=1):
    """
    Apply an intensity scaling to a sampler function

    Parameters
    ----------
    sampler : function
       A function of (lon, lat) that samples a dataset

    vmin : float
      The data value to assign to black
    vmin : float
      The data value to assign to white
    bias : float between 0-1. Default=0.5
      Where to assign middle-grey, relative to (vmin, vmax).
    contrast : float, default=1
      How quickly to ramp from black to white. The default of 1
      ramps over a data range of (vmax - vmin)
    scaling : 'linear' | 'log' | 'arcsinh' | 'sqrt' | 'power'
      The type of intensity scaling to apply

    Returns
    -------
    A function of (lon, lat) that samples an image,
    scales the intensity, and returns an array of dtype=np.uint8
    """
    def result(x, y):
        raw = sampler(x, y)
        r = normalize(raw, vmin, vmax, bias, contrast, scaling)
        return r
    return result
