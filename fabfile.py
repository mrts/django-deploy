"""
This fabfile automates deployment of and moving data between Django apps
in development (devel), staging (stage), and production (live)
environments.

Use it as:

    fab -H user@host:port deploy:stage

See:

 * `requirements.txt` for programs and Python packages that
   are required for the fabfile to work,

 * `README.devel.rst` for guidelines on project layout and development
   process,

 * `README.setup.rst` for guidelines on environment setup.

Additionally, fabfile_conf.py is required with the following strings:

    * PROJECT_NAME
    * PROJECT_BASE_PATH <-- (absolute path to project root)
    * BACKUP_DIR    <-- (should be 'backups')
    * UPLOADS_DIR   <-- (should be os.path.join('media', 'uploads'))
    * SRC_DIR       <-- (should be 'src')
    * CACHE_CLEAR_MODELS <-- (either None or see ./manage.py help cache_clear)
"""

from __future__ import with_statement
import os, tempfile
from fabric import api as fab
from fabric.utils import abort

from fabfile_conf import *

WHEREAMI = os.path.dirname(os.path.abspath(__file__))

class ProjectEnvironment(object):
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self._db_conf = None
        self._django_version = None

    def pull_updates(self):
        with fab.cd(os.path.join(self.path, SRC_DIR)):
            fab.run("git pull")

    def reset_data_from(self, other_env):
        if not _yes("Reset database '%s' content from '%s'?" %
                (self.name, other_env.name)):
            print "Not resetting database content."
            return

        self.clear_cache()

        db_file = other_env.backup_database()
        self.clear_database()
        self.load_database_from(db_file)

        if not _yes("Copy media files from '%s' to '%s'?" %
                (self.name, other_env.name)):
            print "Not copying media files."
            return

        with fab.cd(self.projdir):
            other_uploads_dir = os.path.join(other_env.projdir, UPLOADS_DIR)
            fab.run('mv %s %s.bak'  % (UPLOADS_DIR, UPLOADS_DIR))
            fab.run('cp -a %s %s'    % (other_uploads_dir, UPLOADS_DIR))
            fab.run('rm -r %s.bak'  % UPLOADS_DIR)

    def backup_data(self):
        db_backup_file = self.backup_database()
        upload_backup_dir = self.backup_uploads()
        return db_backup_file, upload_backup_dir

    def migrate_database(self):
        with fab.cd(self.projdir):
            fab.run('./manage.py migrate')

    def reload_wsgi(self):
        with fab.cd(os.path.join(self.path, SRC_DIR)):
            fab.run('touch app.wsgi')

    def clear_cache(self):
        if CACHE_CLEAR_MODELS:
            print "Clearing cache"
            with fab.cd(self.projdir):
                fab.run('./manage.py cache_clear %s' % CACHE_CLEAR_MODELS)
        else:
            print "CACHE_CLEAR_MODELS is empty, not clearing cache"

    @property
    def projdir(self):
        return os.path.join(self.path, SRC_DIR, PROJECT_NAME)

    @property
    def backupdir(self):
        return os.path.join(self.path, BACKUP_DIR)

    def backup_uploads(self):
        uploads_dir = os.path.join(self.projdir, UPLOADS_DIR)
        uploads_backup_dir = os.path.join(self.backupdir,
                '%s_%s_uploads' % (PROJECT_NAME, self.name))
        fab.run('rdiff-backup %s %s' % (uploads_dir, uploads_backup_dir))
        return uploads_backup_dir

    def backup_database(self):
        with fab.cd(self.projdir):
            backup_file_prefix = os.path.join(self.backupdir,
                    'db_backup_%s_%s' % (PROJECT_NAME, self.name))
            result = fab.run('./manage.py db_backup %s' % backup_file_prefix)
            assert (result.succeeded and
                    result.find("successfully backed up to:") > 0)
            actual_backup_file = result.split(':', 1)[1].strip()
            return actual_backup_file

    def load_database_from(self, other_db_file):
        with fab.cd(self.projdir):
            fab.run('./manage.py db_load --noinput %s' % other_db_file)

    def clear_database(self):
        with fab.cd(self.projdir):
            fab.run('./manage.py db_clear --noinput')

    def _bootstrap_django(self):
        if self._django_version is None:
            with fab.cd(self.projdir):
                self._django_version = _bootstrap_django()
        return self._django_version

    @property
    def db_conf(self):
        if self._db_conf is None:
            self._db_conf = self._get_db_conf()
        return self._db_conf

    def _get_db_conf(self):
        version = self._bootstrap_django()
        from django.conf import settings
        db_conf = {
            'engine': settings.DATABASE_ENGINE,
            'db_name': settings.DATABASE_NAME,
            'user': settings.DATABASE_USER,
            'password': settings.DATABASE_PASSWORD,
            'host': settings.DATABASE_HOST,
            'port': settings.DATABASE_PORT,
        } if version < (1, 2) else {
            'engine': (settings.DATABASES['default']['ENGINE']
                .rsplit('.', 1)[-1]),
            'db_name': settings.DATABASES['default']['NAME'],
            'user': settings.DATABASES['default']['USER'],
            'password': settings.DATABASES['default']['PASSWORD'],
            'host': settings.DATABASES['default']['HOST'],
            'port': settings.DATABASES['default']['PORT'],
        }
        return db_conf


ENVIRONMENTS = {
        'stage': ProjectEnvironment('stage',
            path=os.path.join(PROJECT_BASE_PATH, 'stage')),
        'live':  ProjectEnvironment('live',
            path=os.path.join(PROJECT_BASE_PATH, 'live')),
}

def deploy(variant):
    """Deploy latest changes from version control, backup or reset then migrate
    the database and finally reload the WSGI application."""
    env = ENVIRONMENTS[variant]
    env.pull_updates()
    if variant == 'stage':
        env.reset_data_from(ENVIRONMENTS['live'])
    elif variant == 'live':
        if _yes("Backup dabase?"):
            env.backup_database()
        if _yes("Backup uploads?"):
            env.backup_uploads()
    env.migrate_database()
    env.reload_wsgi()

def clear_cache(variant):
    """Clear database cache by invoking cache_clear."""
    env = ENVIRONMENTS[variant]
    env.clear_cache()

def fetch_data(variant):
    """Fetch database content and optionally media from the given environment
    to local (presumably development) environment."""
    if len(fab.env.hosts) != 1:
        abort("Use this with a single host")
    project_dir = os.path.join(WHEREAMI, SRC_DIR, PROJECT_NAME)

    env = ENVIRONMENTS[variant]
    db_backup_file, upload_backup_dir = env.backup_data()
    local_db_file = os.path.join(tempfile.gettempdir(),
            os.path.basename(db_backup_file))
    fab.get(db_backup_file, local_db_file)

    if not _yes("Reset local database content from '%s'?" % variant):
        return
    fab.local('cd %s; ./manage.py db_clear --noinput' % project_dir)
    fab.local('cd %s; ./manage.py db_load %s --noinput' %
            (project_dir, local_db_file))

    if _yes("Fetch uploads as well?"):
        # NOTE: it seems there is no easy way to specify the port
        # for rdiff-backup, therefore we strip it off and expect
        # you to configure it in ~/.ssh/config:
        #
        #   Host my.host.name
        #     Port 1234
        #
        host = fab.env.hosts[0].split(':', 1)[0]

        # TODO: consider if media folder should be removed before
        fab.local('cd %(projdir)s; rdiff-backup --force -r now '
                '%(host)s::%(remotepath)s %(localpath)s' %
                    {'projdir': project_dir,
                     'host': host,
                     'remotepath': upload_backup_dir,
                     'localpath': os.path.join(project_dir, UPLOADS_DIR),})

# --- generic helpers for fab ---

class NonZeroExit(RuntimeError):
    pass

def _sudo(cmd, **kwargs):
    _doit(fab.sudo, cmd, kwargs)

def _run(cmd, **kwargs):
    _doit(fab.run, cmd, kwargs)

def _doit(f, cmd, kwargs):
    with fab.settings(warn_only=True):
        result = f(cmd, **kwargs)
    if result.failed:
        raise NonZeroExit

def _yes(question):
    return fab.prompt(question + " (y/n)", default='n') == 'y'

def _bootstrap_django():
    from django.core.management import setup_environ
    import settings as proj_settings
    setup_environ(proj_settings)
    import django
    return django.VERSION[:2]
