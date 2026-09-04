"""Import the dashboard bundle with overwrite semantics (Superset assets import).

Runs inside the superset image (see the superset-init service). The
`superset import-dashboards` CLI treats charts/datasets/databases as
*dependencies* of the dashboard and never overwrites existing ones, which
breaks bundle iteration; ``ImportAssetsCommand`` is Superset's supported
source-control sync path and "will overwrite everything" by UUID.

Usage: python import_assets.py <bundle.zip> <admin username>
"""

import sys
from zipfile import ZipFile

from flask import g
from superset.app import create_app

app = create_app()
with app.app_context():
    from superset.commands.importers.v1.assets import ImportAssetsCommand
    from superset.commands.importers.v1.utils import get_contents_from_bundle

    from superset import security_manager

    bundle_path, username = sys.argv[1], sys.argv[2]
    g.user = security_manager.find_user(username=username)
    with ZipFile(bundle_path) as bundle:
        contents = get_contents_from_bundle(bundle)
    ImportAssetsCommand(contents).run()
    print(f"assets import complete: {len(contents)} configs")
