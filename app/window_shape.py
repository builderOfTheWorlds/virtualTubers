"""
window_shape.py
Make a borderless X11 window "transparent" around a rendered subject by
clipping the window itself to the subject's silhouette (X SHAPE extension).

WHY SHAPE, NOT ALPHA. The stream is Xvfb captured by ffmpeg x11grab, and
there is no compositing manager on that display — so a 32-bit ARGB window
would be painted opaque (alpha ignored) and x11grab would see a solid
rectangle anyway. SHAPE is enforced by the X server itself: pixels outside
the mask simply do not belong to the window, so whatever is underneath (the
xterm, in whatever theme it is currently wearing) is what gets captured.
The trade-off is a 1-bit edge (no soft anti-aliased fringe), which at a
200px head with the CRT scanline pass on top reads fine.

Everything here is best-effort: ShapeMasker.create() returns None when
libX11/libXext/the SHAPE extension/the window id aren't available, and the
caller falls back to compositing onto a flat background colour.
"""
import ctypes
import ctypes.util
import logging

import numpy as np

log = logging.getLogger(__name__)

#: A pixel is "subject" when its brightest channel is above this. The
#: renderer's clear colour is pure black and apply_codec_screen's grain
#: tops out around 0.015, so 0.08 cleanly separates subject from surround
#: while keeping most of the CRT glow rim.
SILHOUETTE_THRESHOLD = 0.08

# X11 / SHAPE constants (X11/extensions/shape.h)
_SHAPE_BOUNDING = 0
_SHAPE_SET = 0


def silhouette_mask(rgb, threshold=SILHOUETTE_THRESHOLD):
    """(H,W) bool mask of the rendered subject in an un-composited frame.

    Dark interior features (pupils, mouth, shadowed facets) can fall under
    the threshold and would punch holes in the head, so each row is filled
    solid between its first and last subject pixel. A head is near-convex
    per scanline, so this closes the holes without bleeding outside it.
    """
    img = np.asarray(rgb)
    raw = img.max(axis=2) > threshold
    if not raw.any():
        return raw
    width = raw.shape[1]
    has = raw.any(axis=1)
    first = np.where(has, raw.argmax(axis=1), width)
    last = np.where(has, width - 1 - raw[:, ::-1].argmax(axis=1), -1)
    cols = np.arange(width)[None, :]
    return (cols >= first[:, None]) & (cols <= last[:, None])


def pack_bitmap(mask):
    """Pack an (H,W) bool mask into X11 bitmap bytes: LSB-first bits, each
    row padded to a whole byte (XCreateBitmapFromData's format)."""
    return np.packbits(mask.astype(np.uint8), axis=1, bitorder="little").tobytes()


class ShapeMasker:
    """Applies a per-frame bounding-shape mask to one X window."""

    def __init__(self, xlib, xext, display, window):
        self._xlib = xlib
        self._xext = xext
        self._display = display
        self._window = window
        self._last = None

    @classmethod
    def create(cls, window_id):
        """A masker for X window `window_id`, or None if SHAPE can't be used
        here (no libs, no display, server without the extension)."""
        if not window_id:
            log.warning("window_shape: no X window id; transparency disabled")
            return None
        try:
            xlib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
        except OSError as exc:
            log.warning("window_shape: libX11/libXext unavailable (%s)", exc)
            return None

        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XCreateBitmapFromData.restype = ctypes.c_ulong
        xlib.XCreateBitmapFromData.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p,
            ctypes.c_uint, ctypes.c_uint]
        xlib.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XFlush.argtypes = [ctypes.c_void_p]
        xext.XShapeQueryExtension.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        xext.XShapeCombineMask.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_ulong, ctypes.c_int]

        display = xlib.XOpenDisplay(None)
        if not display:
            log.warning("window_shape: XOpenDisplay failed; transparency disabled")
            return None
        ev, err = ctypes.c_int(), ctypes.c_int()
        if not xext.XShapeQueryExtension(display, ctypes.byref(ev), ctypes.byref(err)):
            log.warning("window_shape: X server lacks SHAPE; transparency disabled")
            return None
        log.info("window_shape: SHAPE transparency enabled for window 0x%x", window_id)
        return cls(xlib, xext, display, int(window_id))

    def apply(self, mask):
        """Clip the window to `mask` ((H,W) bool). Skips the X round trip
        when the mask is unchanged from the previous frame."""
        data = pack_bitmap(mask)
        if data == self._last:
            return
        height, width = mask.shape
        pixmap = self._xlib.XCreateBitmapFromData(
            self._display, self._window, data, width, height)
        self._xext.XShapeCombineMask(
            self._display, self._window, _SHAPE_BOUNDING, 0, 0, pixmap, _SHAPE_SET)
        self._xlib.XFreePixmap(self._display, pixmap)
        self._xlib.XFlush(self._display)
        self._last = data
