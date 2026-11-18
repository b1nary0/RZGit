import unittest

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from rzgit.ui.app_icon import ASSETS_FOLDER, ICON_SIZES, app_icon, icon_file
from tests.support import qt_app

HARD_EDGE_MAX = 32  # icon sizes made with hard edges

# Every file the assets folder ships (spec §3).
ASSET_FILES = (
    [f"icon/rzgit-{size}.png" for size in ICON_SIZES]
    + ["icon/rzgit-icon.svg"]
    + [f"logo/rzgit-logo_{v}.svg" for v in ("dark", "light")]
    + [f"logo/rzgit-logo-{h}_{v}.png" for h in (48, 96) for v in ("dark", "light")]
    + [f"splash/{name}_{v}{scale}.png" for name in ("splash", "splash-transparent")
       for v in ("dark", "light") for scale in ("", "@2x")]
)


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_every_asset_file_exists(self):
        missing = [name for name in ASSET_FILES if not (ASSETS_FOLDER / name).is_file()]
        self.assertEqual(missing, [])

    def test_each_icon_png_is_its_size(self):
        for size in ICON_SIZES:
            image = QImage(str(icon_file(size)))
            self.assertFalse(image.isNull(), size)
            self.assertEqual(image.size(), QSize(size, size))
            self.assertTrue(image.hasAlphaChannel(), size)

    def test_small_icons_have_no_semi_transparent_pixels(self):
        # KDE's title bar draws them with colour fringes (see app_icon.py).
        for size in (s for s in ICON_SIZES if s <= HARD_EDGE_MAX):
            image = QImage(str(icon_file(size))).convertToFormat(QImage.Format_ARGB32)
            alphas = {image.pixelColor(x, y).alpha() for x in range(size) for y in range(size)}
            self.assertLessEqual(alphas, {0, 255}, size)

    def test_icon_has_every_size(self):
        icon = app_icon()
        self.assertFalse(icon.isNull())
        self.assertEqual(sorted(s.width() for s in icon.availableSizes()), list(ICON_SIZES))

    def test_icon_picks_the_drawn_size(self):
        # 16 px is its own file, not a bigger size scaled down.
        # Compared premultiplied, the form a QPixmap keeps alpha in.
        pixmap = app_icon().pixmap(QSize(16, 16))
        self.assertEqual(pixmap.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied),
                         QImage(str(icon_file(16))).convertToFormat(QImage.Format_ARGB32_Premultiplied))


if __name__ == "__main__":
    unittest.main()
