"""Empty the product catalogue - every product, live or withdrawn, and its
history - so a product file can be imported into it from a clean slate.

Stop isgPMGService first. Run it with the sleeve store's path, the "store"
line the sleeve tool's --census prints:

    python clearProducts.py "H:\\...\\service\\var\\sleeves.db"

It copies the store beside itself before touching it, asks you to type
CLEAR, and changes nothing else: sleeves, the fee card, the overlay rules,
the funding split and the entities stay as they are. The catalogue's version
number keeps counting, so the next import is a version no delivered proposal
has already used.
"""

import datetime
import os
import shutil
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else input('Paste the sleeve database path (the census "store" line) and press Enter: ')
path = path.strip().strip('"')
if not os.path.isfile(path):
    sys.exit('No file at {}'.format(path))

conn = sqlite3.connect(path)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
if not {'products', 'productHistory', 'sleeveProducts'} <= tables:
    sys.exit('{} is not the sleeve store.'.format(path))

live = conn.execute("SELECT COUNT(*) FROM products WHERE withdrawnAt = ''").fetchone()[0]
withdrawn = conn.execute("SELECT COUNT(*) FROM products WHERE withdrawnAt != ''").fetchone()[0]
history = conn.execute('SELECT COUNT(*) FROM productHistory').fetchone()[0]
print('{}\n  {} live product(s), {} withdrawn, {} history row(s)'.format(
    path, live, withdrawn, history))
if not (live or withdrawn or history):
    sys.exit('The catalogue is already empty.')
if input('Type CLEAR to remove them all: ').strip() != 'CLEAR':
    sys.exit('Nothing was changed.')

backup = '{}.before-clear-{}'.format(path, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
conn.close()
shutil.copy2(path, backup)

conn = sqlite3.connect(path)
with conn:
    conn.execute('DELETE FROM products')
    conn.execute('DELETE FROM productHistory')
conn.close()
print('Cleared. The store before this is kept as {}'.format(backup))
print('Next: import the product file with the sleeve tool (--products-import), then start isgPMGService.')
