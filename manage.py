"""python manage.py reset   -> rebuild the database with fresh demo data
python manage.py empty   -> rebuild with catalogue and users only (no demo data)"""
import sys
import db

if len(sys.argv) > 1 and sys.argv[1] in ("reset", "empty"):
    db.init_db(reset=True, demo=sys.argv[1] == "reset")
    print("Database rebuilt:", db.DB_PATH)
else:
    print(__doc__)
