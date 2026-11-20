"""Settings for the urban green monitor. Everything can be overridden with environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "monitor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "monitor.context.permissions",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        # the analysis runs in a background thread while pages are served
        "OPTIONS": {"timeout": 20},
    }
}

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)  # whitenoise warns when it is missing
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Rasters (NDVI per date, land-cover masks) and rendered overlays
RASTER_ROOT = DATA_DIR / "rasters"

# Sentinel-2 source: Element 84 Earth Search (free, no account), collection 1 L2A
STAC_URL = os.environ.get("STAC_URL", "https://earth-search.aws.element84.com/v1")
STAC_COLLECTION = os.environ.get("STAC_COLLECTION", "sentinel-2-c1-l2a")

# Heat islands: Landsat 8/9 Collection 2 Level-2 (surface temperature) from Microsoft Planetary
# Computer (free, no account; asset URLs are signed with an anonymous SAS token)
HEAT_STAC_URL = os.environ.get("HEAT_STAC_URL", "https://planetarycomputer.microsoft.com/api/stac/v1")
HEAT_COLLECTION = os.environ.get("HEAT_COLLECTION", "landsat-c2-l2")
HEAT_SIGN_URL = os.environ.get("HEAT_SIGN_URL", "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2")

# The OpenStreetMap tile servers refuse requests without a Referer header (tile usage policy).
# Django's default ("same-origin") strips it on cross-origin requests; send only the origin instead.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# On a public server, only staff users (Django admin login) may add communes and start
# analyses: each analysis downloads Sentinel-2 imagery for several minutes.
WRITE_REQUIRES_LOGIN = os.environ.get("WRITE_REQUIRES_LOGIN", "0") == "1"
LOGIN_URL = "admin:login"
# Map images a visitor (not staff) may have computed per hour; already cached images are free.
VISITOR_BUILDS_PER_HOUR = int(os.environ.get("VISITOR_BUILDS_PER_HOUR", "40"))

if not DEBUG:  # behind Nginx Proxy Manager, which terminates HTTPS
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
