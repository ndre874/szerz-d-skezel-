"""
Hajózási Iroda - szerződésnyilvántartó
Zárt rendszer: nem használ külső linkeket, nem fogad és nem küld adatot kívülre.
- Adatok: helyi SQLite (database.db) és files/ mappa
- Ikonok: helyi icons/ mappa
"""
import sys
import os
import re
import html
import math
import shutil
import sqlite3
import atexit
from datetime import datetime

def _current_username():
    """Windows felhasználónév (USERNAME), más rendszeren USER vagy üres."""
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""
from PySide6.QtWidgets import *
from PySide6.QtCore import QDate, Qt, QRect, QSize, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QPainterPath, QTextDocument, QTextCharFormat, QTextCursor
from PySide6.QtPrintSupport import QPrinter
from uuid import uuid4

def _question_igen_nem(parent, title, text, default_no=True):
    """Megerősítő ablak magyar Igen/Nem gombokkal. True = Igen, False = Nem."""
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setIcon(QMessageBox.Icon.Question)
    yes_btn = msg.addButton("Igen", QMessageBox.ButtonRole.YesRole)
    no_btn = msg.addButton("Nem", QMessageBox.ButtonRole.NoRole)
    msg.setDefaultButton(no_btn if default_no else yes_btn)
    msg.setEscapeButton(no_btn)
    msg.exec()
    return msg.clickedButton() == yes_btn

# ===== EXE-nél az indítási mappa, különben a script mappája =====
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    _BUNDLED = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLED = BASE_DIR
DB_PATH = os.path.join(BASE_DIR, "database.db")
FILES_DIR = os.path.join(BASE_DIR, "files")
ICONS_DIR = os.path.join(_BUNDLED, "icons")
LOCK_PATH = os.path.join(BASE_DIR, ".szerzodes_app.running")

os.makedirs(FILES_DIR, exist_ok=True)

# ================= APP LOCK (közös meghajtón egyszerre csak egy példány, .lock kiterjesztés nélkül) =================

def _acquire_app_lock():
    """
    Futtatási zár közös meghajtón: egyszerre csak egy felhasználó futtathatja.
    Nem használ .lock kiterjesztésű fájlt, hogy ne triggerelje a szerver biztonsági szabályait.
    Returns: (success: bool, fd: int|None)
    """
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False, None
    try:
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                os.close(fd)
                return False, None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                os.close(fd)
                return False, None
    except ImportError:
        pass
    return True, fd

def _release_app_lock(fd):
    if fd is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass

# ================= DB =================

def _contract_folder_name(contract_number, contract_id):
    """Szerződésszám alapú almappa név: fájlbiztos + egyedi (szám_id)."""
    bad = '\\/:*?"<>|'
    clean = "".join(c if c not in bad else "_" for c in (contract_number or "").strip())
    clean = clean.strip(" ._") or "contract"
    return f"{clean}_{contract_id}"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("CREATE TABLE IF NOT EXISTS partners(id INTEGER PRIMARY KEY,name TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,name TEXT,parent_id INTEGER)")

    c.execute("""
    CREATE TABLE IF NOT EXISTS contracts(
        id INTEGER PRIMARY KEY,
        partner_id INTEGER,
        category_id INTEGER,
        contract_number TEXT,
        contract_date TEXT,
        expiry_date TEXT,
        indefinite INTEGER,
        parent_id INTEGER,
        is_mod INTEGER,
        nickname TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY,
        contract_id INTEGER,
        filename TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS contract_notes(
        id INTEGER PRIMARY KEY,
        contract_id INTEGER NOT NULL,
        note_text TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS partner_contacts(
        id INTEGER PRIMARY KEY,
        partner_id INTEGER NOT NULL,
        contact_type TEXT NOT NULL,
        label TEXT,
        value TEXT NOT NULL
    )""")

    # Migráció: ha a contracts tábla már létezik régi sémával, adjuk hozzá a hiányzó oszlopokat
    cols = [row[1] for row in c.execute("PRAGMA table_info(contracts)").fetchall()]
    if "is_mod" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN is_mod INTEGER")
    if "parent_id" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN parent_id INTEGER")
    if "requires_deposit" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN requires_deposit INTEGER DEFAULT 0")
    if "deposit_amount" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN deposit_amount REAL")
    if "monthly_fee" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN monthly_fee REAL")
    if "updated_at" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN updated_at TEXT")
    if "deposit_required" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN deposit_required REAL")
    if "deposit_status" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN deposit_status TEXT")
    if "monthly_fee_indexed" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN monthly_fee_indexed INTEGER DEFAULT 0")
    if "monthly_fee_indexed_year" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN monthly_fee_indexed_year INTEGER")
    if "penzügyi_locked" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN penzügyi_locked INTEGER DEFAULT 0")
    if "nickname" not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN nickname TEXT")

    # Migráció: categories – 4 szintű hierarchia (parent_id, UNIQUE eltávolítva)
    try:
        cat_cols = [row[1] for row in c.execute("PRAGMA table_info(categories)").fetchall()]
        if "parent_id" not in cat_cols:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("CREATE TABLE categories_new(id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER)")
            c.execute("INSERT INTO categories_new(id, name, parent_id) SELECT id, name, NULL FROM categories")
            c.execute("DROP TABLE categories")
            c.execute("ALTER TABLE categories_new RENAME TO categories")
            c.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS contract_modules(
        contract_id INTEGER NOT NULL,
        module_name TEXT NOT NULL,
        PRIMARY KEY(contract_id, module_name)
    )""")
    # Migráció: meglévő szerződések pénzügyi adataihoz engedélyezés a penzügyi modul
    try:
        for row in c.execute("""
            SELECT id FROM contracts WHERE requires_deposit=1 OR monthly_fee IS NOT NULL 
            OR deposit_required IS NOT NULL OR deposit_amount IS NOT NULL
        """).fetchall():
            cid = row[0]
            c.execute("INSERT OR IGNORE INTO contract_modules(contract_id, module_name) VALUES(?, 'penzügyi')", (cid,))
    except Exception:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS contract_module_data(
        contract_id INTEGER NOT NULL,
        module_name TEXT NOT NULL,
        data_text TEXT,
        PRIMARY KEY(contract_id, module_name)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS contract_muszaki_files(
        contract_id INTEGER NOT NULL,
        stored_path TEXT NOT NULL,
        PRIMARY KEY(contract_id, stored_path)
    )""")

    # Migráció: contract_notes – felhasználónév
    try:
        note_cols = [row[1] for row in c.execute("PRAGMA table_info(contract_notes)").fetchall()]
        if "created_by" not in note_cols:
            c.execute("ALTER TABLE contract_notes ADD COLUMN created_by TEXT")
    except Exception:
        pass

    # Migráció: régi fájlok -> contract almappák (files/{contract_id}/)
    for row in c.execute("SELECT id, contract_id, filename FROM files").fetchall():
        fid, cid, fname = row
        if "/" in fname:
            continue
        old_path = os.path.join(FILES_DIR, fname)
        new_dir = os.path.join(FILES_DIR, str(cid))
        new_path = os.path.join(new_dir, fname)
        if os.path.isfile(old_path):
            os.makedirs(new_dir, exist_ok=True)
            try:
                shutil.move(old_path, new_path)
                c.execute("UPDATE files SET filename=? WHERE id=?", (f"{cid}/{fname}", fid))
            except Exception:
                pass

    # Migráció: files/{id}/ -> files/{szerződésszám_id}/
    for row in c.execute("SELECT id, contract_id, filename FROM files").fetchall():
        fid, cid, fname = row
        if "/" not in fname:
            continue
        folder_old = fname.split("/")[0]
        if not folder_old.isdigit():
            continue
        num_row = c.execute("SELECT contract_number FROM contracts WHERE id=?", (cid,)).fetchone()
        contract_number = (num_row[0] or "").strip() if num_row else ""
        new_folder = _contract_folder_name(contract_number, cid)
        old_dir = os.path.join(FILES_DIR, folder_old)
        new_dir = os.path.join(FILES_DIR, new_folder)
        if os.path.isdir(old_dir) and old_dir != new_dir:
            try:
                os.makedirs(new_dir, exist_ok=True)
                for f in os.listdir(old_dir):
                    src = os.path.join(old_dir, f)
                    if os.path.isfile(src):
                        shutil.move(src, os.path.join(new_dir, f))
                shutil.rmtree(old_dir, ignore_errors=True)
                new_fname = f"{new_folder}/{os.path.basename(fname)}"
                c.execute("UPDATE files SET filename=? WHERE id=?", (new_fname, fid))
            except Exception:
                pass

    conn.commit()
    conn.close()

def resolve_contract_file(contract_id, stored_filename):
    """
    Megkeresi a szerződés fájlját. Ha a DB-ban tárolt elérés nem létezik,
    a szerződés almappájában (files/{szám_id}/) keres és frissíti a DB-t.
    """
    if not stored_filename:
        return None, False
    path = os.path.join(FILES_DIR, stored_filename)
    if os.path.isfile(path):
        return path, True
    # Mappa a stored_filename alapján (pl. "SZ-2024_42/doc.pdf" -> SZ-2024_42)
    folder_name = stored_filename.split("/")[0] if "/" in stored_filename else None
    if not folder_name:
        return None, False
    contract_dir = os.path.join(FILES_DIR, folder_name)
    if not os.path.isdir(contract_dir):
        # Régi struktúra: mappa név szerint lehet {id} vagy {szám_id}; keress _contract_id végződést
        for name in os.listdir(FILES_DIR):
            if name.endswith("_" + str(contract_id)) and os.path.isdir(os.path.join(FILES_DIR, name)):
                contract_dir = os.path.join(FILES_DIR, name)
                folder_name = name
                break
        else:
            return None, False
    files_in_dir = [f for f in os.listdir(contract_dir)
                    if os.path.isfile(os.path.join(contract_dir, f)) and not f.startswith(".")]
    if not files_in_dir:
        return None, False
    chosen = max(files_in_dir, key=lambda x: os.path.getmtime(os.path.join(contract_dir, x)))
    new_stored = f"{folder_name}/{chosen}"
    conn = db()
    try:
        conn.execute("UPDATE files SET filename=? WHERE contract_id=? AND filename=?", (new_stored, contract_id, stored_filename))
        conn.commit()
    finally:
        conn.close()
    return os.path.join(contract_dir, chosen), True

# ================= LIST EDITOR =================

class ListDialog(QDialog):
    def __init__(self, table):
        super().__init__()
        self.table = table
        self.setWindowTitle("Lista kezelése")
        self.resize(400,400)

        v = QVBoxLayout()

        self.list = QListWidget()
        self.input = QLineEdit()

        add = QPushButton("Hozzáadás")
        self.edit_btn = QPushButton("Átnevezés")
        delete = _delete_button("Törlés")

        add.clicked.connect(self.add)
        self.edit_btn.clicked.connect(self.edit)
        delete.clicked.connect(self.delete)

        self.details_btn = QPushButton("Szerkesztés")
        self.details_btn.clicked.connect(self.open_details)

        v.addWidget(self.list)
        v.addWidget(self.input)
        v.addWidget(add)
        if table != "partners":
            v.addWidget(self.edit_btn)
        v.addWidget(delete)
        if table == "partners":
            v.addWidget(self.details_btn)

        self.setLayout(v)
        self.load()

    def load(self):
        self.list.clear()
        conn = db()
        try:
            c = conn.cursor()
            for row in c.execute(f"SELECT id,name FROM {self.table}"):
                item = QListWidgetItem(row[1])
                item.setData(Qt.UserRole, row[0])
                self.list.addItem(item)
        finally:
            conn.close()

    def add(self):
        t = self.input.text().strip()
        if not t: return
        c = db(); cur = c.cursor()
        try:
            cur.execute(f"INSERT INTO {self.table}(name) VALUES(?)",(t,))
            c.commit()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Hiba", "Ez a név már létezik.")
        finally:
            c.close()
        self.input.clear()
        self.load()

    def edit(self):
        item = self.list.currentItem()
        if not item: return
        t = self.input.text().strip()
        if not t: return
        id = item.data(Qt.UserRole)
        c = db(); cur = c.cursor()
        try:
            cur.execute(f"UPDATE {self.table} SET name=? WHERE id=?",(t,id))
            c.commit()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Hiba", "Ez a név már létezik.")
        finally:
            c.close()
        self.load()

    def delete(self):
        item=self.list.currentItem()
        if not item: return
        id = item.data(Qt.UserRole)
        c = db(); cur = c.cursor()
        try:
            if self.table == "partners":
                used = cur.execute("SELECT COUNT(*) FROM contracts WHERE partner_id=?", (id,)).fetchone()[0]
                if used:
                    QMessageBox.warning(self, "Hiba", "A szerződő fél nem törölhető, mert már van hozzá szerződés.")
                    return
            if self.table == "categories":
                used = cur.execute("SELECT COUNT(*) FROM contracts WHERE category_id=?", (id,)).fetchone()[0]
                if used:
                    QMessageBox.warning(self, "Hiba", "A kategória nem törölhető, mert már van hozzá szerződés.")
                    return

            cur.execute(f"DELETE FROM {self.table} WHERE id=?", (id,))
            c.commit()
        finally:
            c.close()
        self.load()

    def open_details(self):
        if self.table != "partners":
            return
        item = self.list.currentItem()
        if not item:
            QMessageBox.information(self, "Szerkesztés", "Válasszon szerződő felet a listából.")
            return
        pid = item.data(Qt.UserRole)
        d = PartnerDetailDialog(self, pid)
        d.exec()
        self.load()

# ================= KATEGÓRIA HELPEREK (4 szint) =================

def _category_path(category_id):
    """Kategória teljes útvonala: 'Fő > Alk1 > Alk2 > Alk3'."""
    if not category_id:
        return ""
    conn = db()
    try:
        path = []
        cid = category_id
        while cid:
            r = conn.execute("SELECT name, parent_id FROM categories WHERE id=?", (cid,)).fetchone()
            if not r:
                break
            path.insert(0, r[0] or "")
            cid = r[1]
        return " > ".join(p for p in path if p)
    finally:
        conn.close()

def _category_children(parent_id):
    """parent_id alatti kategóriák: [(id, name), ...]. parent_id=None = főkategóriák."""
    conn = db()
    try:
        if parent_id is None:
            rows = conn.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT id, name FROM categories WHERE parent_id=? ORDER BY name", (parent_id,)).fetchall()
        return [(r[0], r[1] or "") for r in rows]
    finally:
        conn.close()

def _category_descendant_ids(category_id):
    """Kategória és minden leszármazottja ID-ja (rekurzív)."""
    ids = [category_id]
    for cid, _ in _category_children(category_id):
        ids.extend(_category_descendant_ids(cid))
    return ids

def _all_categories_for_combo():
    """Összes kategória teljes útvonallal: [(None, 'Összes elem'), (id, path), ...]."""
    result = [(None, "Összes elem")]
    def add_with_children(parent_id, prefix):
        for cid, cname in _category_children(parent_id):
            path = f"{prefix}{cname}" if prefix else cname
            result.append((cid, path))
            add_with_children(cid, f"{path} > ")
    add_with_children(None, "")
    return result

# ================= KATEGÓRIÁK KEZELÉSE =================

class CategoryListDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kategóriák kezelése")
        self.resize(480, 480)
        layout = QVBoxLayout()

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Kategória"])
        self.tree.setMinimumHeight(200)
        layout.addWidget(self.tree)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Név:"))
        self.input = QLineEdit()
        self.input.setPlaceholderText("Új kategória neve")
        add_row.addWidget(self.input)
        add_row.addWidget(QLabel("Szint:"))
        self.level_combo = QComboBox()
        self.level_combo.addItems(["1 – Fő kategória", "2 – Alkategória 1", "3 – Alkategória 2", "4 – Alkategória 3"])
        self.level_combo.currentIndexChanged.connect(self._on_level_change)
        add_row.addWidget(self.level_combo)
        self.parent_combo = QComboBox()
        self.parent_combo.setMinimumWidth(180)
        self._load_parent_options()
        add_row.addWidget(QLabel("Szülő:"))
        add_row.addWidget(self.parent_combo)
        add_row.addStretch()
        add_btn = QPushButton("Hozzáadás")
        add_btn.clicked.connect(self.add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        edit_row = QHBoxLayout()
        self.edit_input = QLineEdit()
        self.edit_input.setPlaceholderText("Átnevezés mezője")
        edit_btn = QPushButton("Átnevezés")
        edit_btn.clicked.connect(self.edit)
        edit_row.addWidget(self.edit_input)
        edit_row.addWidget(edit_btn)
        layout.addLayout(edit_row)

        del_btn = _delete_button("Törlés")
        del_btn.clicked.connect(self.delete)
        layout.addWidget(del_btn)

        self.setLayout(layout)
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        self._on_level_change()
        self.load()

    def _on_tree_selection(self, current, _previous):
        self.edit_input.clear()
        if current:
            self.edit_input.setText(current.text(0))

    def _on_level_change(self):
        self._load_parent_options()

    def _load_parent_options(self):
        level = self.level_combo.currentIndex() + 1
        self.parent_combo.clear()
        if level == 1:
            self.parent_combo.setEnabled(False)
            self.parent_combo.addItem("—", None)
        else:
            self.parent_combo.setEnabled(True)
            self._fill_parent_combo(None, level - 1, 0)

    def _fill_parent_combo(self, parent_id, depth, indent):
        if depth <= 0:
            return
        prefix = "  " * indent
        for cid, cname in _category_children(parent_id):
            self.parent_combo.addItem(prefix + cname, cid)
            if depth > 1:
                self._fill_parent_combo(cid, depth - 1, indent + 1)

    def load(self):
        self.tree.clear()
        def add_nodes(parent_widget, parent_id):
            for cid, cname in _category_children(parent_id):
                item = QTreeWidgetItem([cname])
                item.setData(0, Qt.UserRole, cid)
                parent_widget.addChild(item) if parent_widget else self.tree.addTopLevelItem(item)
                add_nodes(item, cid)
        add_nodes(None, None)

    def add(self):
        name = self.input.text().strip()
        if not name:
            QMessageBox.warning(self, "Hiba", "A kategória neve nem lehet üres.")
            return
        level = self.level_combo.currentIndex() + 1
        parent_id = self.parent_combo.currentData() if level > 1 else None
        if level > 1 and parent_id is None:
            QMessageBox.warning(self, "Hiba", "Válasszon szülő kategóriát.")
            return
        conn = db()
        try:
            conn.execute("INSERT INTO categories(name, parent_id) VALUES(?, ?)", (name, parent_id))
            conn.commit()
            self.input.clear()
            self.load()
            self._load_parent_options()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Hiba", "Ez a kategória már létezik ezen a szinten.")
        finally:
            conn.close()

    def edit(self):
        item = self.tree.currentItem()
        if not item:
            QMessageBox.information(self, "Átnevezés", "Válasszon ki egy kategóriát a listából.")
            return
        new_name = self.edit_input.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Hiba", "A név nem lehet üres.")
            return
        cid = item.data(0, Qt.UserRole)
        conn = db()
        try:
            conn.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cid))
            conn.commit()
            self.edit_input.clear()
            self.load()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Hiba", "Ez a név már létezik.")
        finally:
            conn.close()

    def delete(self):
        item = self.tree.currentItem()
        if not item:
            QMessageBox.information(self, "Törlés", "Válasszon ki egy kategóriát a listából.")
            return
        cid = item.data(0, Qt.UserRole)
        ids_to_delete = _category_descendant_ids(cid)
        conn = db()
        try:
            for cat_id in ids_to_delete:
                used = conn.execute("SELECT COUNT(*) FROM contracts WHERE category_id=?", (cat_id,)).fetchone()[0]
                if used:
                    QMessageBox.warning(self, "Hiba", "A kategória nem törölhető, mert van hozzá szerződés (a kategória vagy valamely alkategóriája).")
                    return
            child_count = len(ids_to_delete) - 1
            if child_count > 0:
                if not _question_igen_nem(
                    self, "Törlés megerősítése",
                    f"A törölni kívánt kategória alatt {child_count} alkategória van.\n"
                    "Minden hozzá kapcsolódó alkategória is törölve lesz. Folytatja?",
                ):
                    return
            for cat_id in reversed(ids_to_delete):
                conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
            conn.commit()
            self.load()
        finally:
            conn.close()

def _main_contracts_for_combo():
    """Fő szerződések (parent_id IS NULL) combohoz: [(id, 'Partner – Szerződésszám'), ...]."""
    conn = db()
    try:
        rows = conn.execute("""
            SELECT c.id, p.name, c.contract_number
            FROM contracts c
            JOIN partners p ON p.id = c.partner_id
            WHERE c.parent_id IS NULL
            ORDER BY p.name, c.contract_date DESC, c.id DESC
        """).fetchall()
        return [(r[0], f"{r[1]} – {r[2] or ''}") for r in rows]
    finally:
        conn.close()

def _contract_has_modifications(contract_id):
    """Van-e a szerződéshez módosítás (gyerek sor)."""
    conn = db()
    try:
        n = conn.execute("SELECT COUNT(*) FROM contracts WHERE parent_id=?", (contract_id,)).fetchone()[0]
        return n > 0
    finally:
        conn.close()

def _contract_is_modification(contract_id):
    """A szerződés egy módosítás (van parent_id)."""
    conn = db()
    try:
        r = conn.execute("SELECT parent_id FROM contracts WHERE id=?", (contract_id,)).fetchone()
        return r and r[0] is not None
    finally:
        conn.close()

def _modification_is_latest(contract_id):
    """Ha a szerződés módosítás, akkor ez a legfrissebb (utolsó) módosítás az eredeti alatt?"""
    conn = db()
    try:
        r = conn.execute("SELECT parent_id FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if not r or r[0] is None:
            return True
        parent_id = r[0]
        last_id = conn.execute(
            """SELECT id FROM contracts WHERE parent_id=? ORDER BY contract_date ASC, id ASC""",
            (parent_id,),
        ).fetchall()
        if not last_id:
            return True
        return last_id[-1][0] == contract_id
    finally:
        conn.close()

# ================= SZERZŐDÉS MODULOK =================

CONTRACT_MODULES = [
    ("penzügyi", "Pénzügyi adatok"),
    ("muszaki", "Média"),
]

def _contract_enabled_modules(contract_id):
    """Szerződésen engedélyezett modulok: [module_name, ...]."""
    conn = db()
    try:
        return [r[0] for r in conn.execute(
            "SELECT module_name FROM contract_modules WHERE contract_id=? ORDER BY module_name",
            (contract_id,),
        ).fetchall()]
    finally:
        conn.close()

def _add_contract_module(contract_id, module_name):
    conn = db()
    try:
        conn.execute("INSERT OR IGNORE INTO contract_modules(contract_id, module_name) VALUES(?, ?)", (contract_id, module_name))
        conn.commit()
    finally:
        conn.close()

def _remove_contract_module(contract_id, module_name):
    conn = db()
    try:
        conn.execute("DELETE FROM contract_modules WHERE contract_id=? AND module_name=?", (contract_id, module_name))
        conn.commit()
    finally:
        conn.close()

def _get_module_data(contract_id, module_name):
    conn = db()
    try:
        r = conn.execute("SELECT data_text FROM contract_module_data WHERE contract_id=? AND module_name=?", (contract_id, module_name)).fetchone()
        return r[0] if r else ""
    finally:
        conn.close()

def _set_module_data(contract_id, module_name, data_text):
    conn = db()
    try:
        conn.execute("INSERT OR REPLACE INTO contract_module_data(contract_id, module_name, data_text) VALUES(?, ?, ?)", (contract_id, module_name, data_text or ""))
        conn.commit()
    finally:
        conn.close()

def _muszaki_folder(contract_id):
    """Szerződés műszaki almappája: (folder_name, teljes_mappapath). Nincs mappa ha nincs contract_number."""
    r = db().execute("SELECT contract_number FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not r or not (r[0] or "").strip():
        return None, None
    folder_name = _contract_folder_name((r[0] or "").strip(), contract_id)
    full_dir = os.path.join(FILES_DIR, folder_name, "muszaki")
    return folder_name, full_dir

def _muszaki_add_file(contract_id, source_path):
    """Fájl másolása a műszaki mappába, DB-be rögzítés. Vissza: stored_path vagy None."""
    folder_name, full_dir = _muszaki_folder(contract_id)
    if not full_dir:
        return None
    os.makedirs(full_dir, exist_ok=True)
    base = os.path.basename(source_path)
    stored_name = f"{uuid4().hex}_{base}"
    stored_path = f"{folder_name}/muszaki/{stored_name}"
    dest = os.path.join(full_dir, stored_name)
    try:
        shutil.copy2(source_path, dest)
    except OSError:
        return None
    conn = db()
    try:
        conn.execute("INSERT OR IGNORE INTO contract_muszaki_files(contract_id, stored_path) VALUES(?, ?)", (contract_id, stored_path))
        conn.commit()
    finally:
        conn.close()
    return stored_path

def _muszaki_display_name(stored_path):
    """Csak a fájl neve, uuid_ prefix nélkül: 'hex_eredeti.pdf' -> 'eredeti.pdf'."""
    base = os.path.basename(stored_path)
    if "_" in base:
        prefix, rest = base.split("_", 1)
        if len(prefix) == 32 and all(c in "0123456789abcdef" for c in prefix.lower()):
            return rest
    return base

def _muszaki_list_files(contract_id):
    """Műszaki fájlok listája: [(stored_path, display_name), ...]."""
    conn = db()
    try:
        rows = conn.execute("SELECT stored_path FROM contract_muszaki_files WHERE contract_id=? ORDER BY stored_path", (contract_id,)).fetchall()
        return [(r[0], _muszaki_display_name(r[0])) for r in rows]
    finally:
        conn.close()

def _muszaki_remove_file(contract_id, stored_path):
    """Egy műszaki fájl törlése a lemezen és a DB-ből."""
    full = os.path.join(FILES_DIR, stored_path)
    if os.path.isfile(full):
        try:
            os.remove(full)
        except OSError:
            pass
    conn = db()
    try:
        conn.execute("DELETE FROM contract_muszaki_files WHERE contract_id=? AND stored_path=?", (contract_id, stored_path))
        conn.commit()
    finally:
        conn.close()

def _muszaki_delete_all(contract_id):
    """Műszaki modul összes fájljának és DB bejegyzésének törlése."""
    for stored_path, _ in _muszaki_list_files(contract_id):
        _muszaki_remove_file(contract_id, stored_path)
    folder_name, full_dir = _muszaki_folder(contract_id)
    if full_dir and os.path.isdir(full_dir):
        try:
            shutil.rmtree(full_dir, ignore_errors=True)
        except OSError:
            pass

# ================= PARTNER RÉSZLETEK (telefon, email) =================

def _partner_contacts(partner_id):
    """Partner elérhetőségei: [(type, label, value), ...]"""
    conn = db()
    try:
        return conn.execute(
            "SELECT contact_type, label, value FROM partner_contacts WHERE partner_id=? ORDER BY contact_type, id",
            (partner_id,),
        ).fetchall()
    finally:
        conn.close()

class PartnerDetailDialog(QDialog):
    def __init__(self, parent=None, partner_id=None):
        super().__init__(parent)
        self.partner_id = partner_id
        self.setWindowTitle("Szerződő fél adatkezelő")
        self.resize(460, 440)

        layout = QVBoxLayout()
        r = db().execute("SELECT name FROM partners WHERE id=?", (partner_id,)).fetchone()
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Szerződő fél neve:"))
        self.name_edit = QLineEdit(r[0] if r else "")
        self.name_edit.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.name_edit.setMinimumWidth(200)
        rename_btn = QPushButton("Átnevezés")
        rename_btn.clicked.connect(self._rename_partner)
        name_row.addWidget(self.name_edit)
        name_row.addWidget(rename_btn)
        name_row.addStretch()
        layout.addLayout(name_row)

        grp = QGroupBox("Telefonszámok")
        ph_layout = QVBoxLayout()
        self.phone_list = QListWidget()
        self.phone_list.setMinimumHeight(100)
        ph_row = QHBoxLayout()
        self.phone_label = QLineEdit()
        self.phone_label.setPlaceholderText("Név (pl. Iroda, Mobil)")
        self.phone_label.setMaximumWidth(140)
        self.phone_value = QLineEdit()
        self.phone_value.setPlaceholderText("Telefonszám")
        self.phone_value.setMaximumWidth(160)
        ph_add = QPushButton("Hozzáadás")
        ph_add.clicked.connect(self.add_phone)
        ph_row.addWidget(self.phone_label)
        ph_row.addWidget(self.phone_value)
        ph_row.addWidget(ph_add)
        ph_layout.addWidget(self.phone_list)
        ph_layout.addLayout(ph_row)
        grp.setLayout(ph_layout)
        layout.addWidget(grp)

        grp2 = QGroupBox("E-mail címek")
        em_layout = QVBoxLayout()
        self.email_list = QListWidget()
        self.email_list.setMinimumHeight(80)
        em_row = QHBoxLayout()
        self.email_label = QLineEdit()
        self.email_label.setPlaceholderText("Név (pl. Fő, Tárgy)")
        self.email_label.setMaximumWidth(140)
        self.email_value = QLineEdit()
        self.email_value.setPlaceholderText("E-mail cím")
        self.email_value.setMaximumWidth(200)
        em_add = QPushButton("Hozzáadás")
        em_add.clicked.connect(self.add_email)
        em_row.addWidget(self.email_label)
        em_row.addWidget(self.email_value)
        em_row.addWidget(em_add)
        em_layout.addWidget(self.email_list)
        em_layout.addLayout(em_row)
        grp2.setLayout(em_layout)
        layout.addWidget(grp2)

        del_btn = _delete_button("Kiválasztott törlése")
        del_btn.clicked.connect(self.delete_selected)
        layout.addWidget(del_btn)

        layout.addStretch()
        save_btn = QPushButton(" Mentés")
        save_btn.setIcon(_minimal_icon("save"))
        save_btn.setIconSize(QSize(14, 14))
        save_btn.clicked.connect(self.accept)
        layout.addWidget(save_btn)

        self.setLayout(layout)
        self.load_contacts()

    def _rename_partner(self):
        new_name = self.name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Hiba", "A szerződő fél neve nem lehet üres.")
            return
        conn = db()
        try:
            conn.execute("UPDATE partners SET name=? WHERE id=?", (new_name, self.partner_id))
            conn.commit()
            QMessageBox.information(self, "Mentve", "A szerződő fél adatai mentve.")
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Hiba", "Ez a név már létezik.")
        finally:
            conn.close()

    def load_contacts(self):
        self.phone_list.clear()
        self.email_list.clear()
        conn = db()
        try:
            for row in conn.execute(
                "SELECT id, contact_type, label, value FROM partner_contacts WHERE partner_id=? ORDER BY contact_type, id",
                (self.partner_id,),
            ).fetchall():
                fid, ctype, label, val = row
                txt = f"{label}: {val}" if label else val
                item = QListWidgetItem(txt)
                item.setData(Qt.UserRole, (fid, ctype))
                if ctype == "phone":
                    self.phone_list.addItem(item)
                else:
                    self.email_list.addItem(item)
        finally:
            conn.close()

    def add_phone(self):
        lbl = self.phone_label.text().strip() or "Telefon"
        val = self.phone_value.text().strip()
        if not val:
            return
        conn = db()
        try:
            conn.execute(
                "INSERT INTO partner_contacts(partner_id, contact_type, label, value) VALUES(?, 'phone', ?, ?)",
                (self.partner_id, lbl, val),
            )
            conn.commit()
        finally:
            conn.close()
        self.phone_label.clear()
        self.phone_value.clear()
        self.load_contacts()

    def add_email(self):
        lbl = self.email_label.text().strip() or "E-mail"
        val = self.email_value.text().strip()
        if not val:
            return
        conn = db()
        try:
            conn.execute(
                "INSERT INTO partner_contacts(partner_id, contact_type, label, value) VALUES(?, 'email', ?, ?)",
                (self.partner_id, lbl, val),
            )
            conn.commit()
        finally:
            conn.close()
        self.email_label.clear()
        self.email_value.clear()
        self.load_contacts()

    def delete_selected(self):
        for lst in (self.phone_list, self.email_list):
            item = lst.currentItem()
            if not item:
                continue
            fid, _ = item.data(Qt.UserRole)
            conn = db()
            try:
                conn.execute("DELETE FROM partner_contacts WHERE id=?", (fid,))
                conn.commit()
            finally:
                conn.close()
            self.load_contacts()
            return
        QMessageBox.information(self, "Törlés", "Válasszon ki egy elemet a listából.")

# ================= NEW CONTRACT =================

class NewContract(QDialog):
    def __init__(self, parent=None, parent_id=None):
        super().__init__(parent)
        self.parent_id = parent_id
        self.setWindowTitle("Új szerződés rögzítése")
        self.resize(400, 380)

        f = QFormLayout()

        self.is_modification = QCheckBox("Már feltöltött szerződéshez tartozó módosítást rögzítek")
        self.is_modification.stateChanged.connect(self._on_modification_toggled)
        f.addRow(self.is_modification)

        self.parent_contract_combo = QComboBox()
        self.parent_contract_combo.setMinimumWidth(280)
        self._fill_parent_combo()
        self.parent_row_label = QLabel("Eredeti szerződés:")
        f.addRow(self.parent_row_label, self.parent_contract_combo)

        self.mod_number_row = QWidget()
        mod_num_layout = QFormLayout()
        self.mod_number = QLineEdit()
        self.mod_number.setPlaceholderText("A módosítás saját szerződésszáma")
        mod_num_layout.addRow("Szerződésszám (módosítás):", self.mod_number)
        self.mod_number_row.setLayout(mod_num_layout)
        f.addRow(self.mod_number_row)

        self.form_widget = QWidget()
        form_inner = QFormLayout()
        self.partner = QComboBox()
        self.cat1 = QComboBox()
        self.cat2 = QComboBox()
        self.cat3 = QComboBox()
        self.cat4 = QComboBox()
        for cb in (self.cat1, self.cat2, self.cat3, self.cat4):
            cb.currentIndexChanged.connect(self._on_cat_change)
        self.number = QLineEdit()
        self.nickname = QLineEdit()
        self.nickname.setMaxLength(20)
        self.nickname.setPlaceholderText("Megnevezés (max. 20 karakter, opcionális)")
        self.date = QDateEdit(QDate.currentDate())
        self.indef = QCheckBox("Határozatlan idejű szerződés")
        self.exp = QDateEdit(QDate.currentDate())
        self.indef.stateChanged.connect(lambda: self.exp.setEnabled(not self.indef.isChecked()))
        form_inner.addRow("Szerződő fél", self.partner)
        form_inner.addRow("Fő kategória", self.cat1)
        form_inner.addRow("Alkategória 1", self.cat2)
        form_inner.addRow("Alkategória 2", self.cat3)
        form_inner.addRow("Alkategória 3", self.cat4)
        form_inner.addRow("Szerződésszám", self.number)
        form_inner.addRow("Megnevezés (opcionális)", self.nickname)
        form_inner.addRow("Szerződéskötés dátuma", self.date)
        form_inner.addRow(self.indef)
        form_inner.addRow("Lejárat dátuma", self.exp)
        self.form_widget.setLayout(form_inner)
        f.addRow(self.form_widget)

        self.mod_date_label = QLabel("Módosítás megkötésének dátuma:")
        self.mod_date = QDateEdit(QDate.currentDate())
        f.addRow(self.mod_date_label, self.mod_date)

        self.mod_indef = QCheckBox("Határozatlan idejű szerződés")
        self.mod_exp = QDateEdit(QDate.currentDate())
        self.mod_indef.stateChanged.connect(lambda: self.mod_exp.setEnabled(not self.mod_indef.isChecked()))
        self.mod_nickname = QLineEdit()
        self.mod_nickname.setMaxLength(20)
        self.mod_nickname.setPlaceholderText("Megnevezés (max. 20 karakter, opcionális)")
        self.mod_exp_row = QWidget()
        mod_exp_layout = QFormLayout()
        mod_exp_layout.addRow(self.mod_indef)
        mod_exp_layout.addRow("Lejárat dátuma:", self.mod_exp)
        mod_exp_layout.addRow("Megnevezés (opcionális)", self.mod_nickname)
        self.mod_exp_row.setLayout(mod_exp_layout)
        f.addRow(self.mod_exp_row)

        self.file_btn = QPushButton("Csatolmány kiválasztása")
        self.file_btn.clicked.connect(self.pick)
        self.file = None
        self.file_label = QLabel("Nincs csatolmány kiválasztva")
        self.file_label.setStyleSheet("color: gray; font-style: italic;")
        self.file_label.setWordWrap(True)
        f.addRow(self.file_btn)
        f.addRow("Szerződés:", self.file_label)

        save = QPushButton("Mentés")
        save.clicked.connect(self.save)
        f.addRow(save)

        self.setLayout(f)
        self.load()
        self._on_modification_toggled()
        self.partner.setCurrentIndex(-1)
        for cb in (self.cat1, self.cat2, self.cat3, self.cat4):
            cb.setCurrentIndex(-1)
        self.number.clear()
        self.partner.currentIndexChanged.connect(lambda: self._set_field_error(self.partner, False))
        for cb in (self.cat1, self.cat2, self.cat3, self.cat4):
            cb.currentIndexChanged.connect(lambda: self._set_field_error(self.cat1, False))
        self.number.textChanged.connect(lambda: self._set_field_error(self.number, False))

    def _fill_parent_combo(self):
        self.parent_contract_combo.clear()
        self.parent_contract_combo.addItem("(válasszon eredeti szerződést)", None)
        for cid, label in _main_contracts_for_combo():
            self.parent_contract_combo.addItem(label, cid)

    def _on_modification_toggled(self):
        is_mod = self.is_modification.isChecked()
        self.form_widget.setVisible(not is_mod)
        self.parent_row_label.setVisible(is_mod)
        self.parent_contract_combo.setVisible(is_mod)
        self.mod_number_row.setVisible(is_mod)
        self.mod_date_label.setVisible(is_mod)
        self.mod_date.setVisible(is_mod)
        self.mod_exp_row.setVisible(is_mod)

    def _set_field_error(self, widget, is_error):
        if is_error:
            widget.setStyleSheet("border: 1px solid red; background: #fff5f5;")
        else:
            widget.setStyleSheet("")

    def _on_cat_change(self):
        sender = self.sender()
        if sender == self.cat1:
            pid = self.cat1.currentData()
            self.cat2.clear()
            self.cat2.addItem("— (nem kötelező)", None)
            if pid is not None:
                for cid, cname in _category_children(pid):
                    self.cat2.addItem(cname, cid)
            self.cat2.setCurrentIndex(0)
            self.cat3.clear()
            self.cat3.addItem("— (nem kötelező)", None)
            self.cat3.setCurrentIndex(0)
            self.cat4.clear()
            self.cat4.addItem("— (nem kötelező)", None)
            self.cat4.setCurrentIndex(0)
        elif sender == self.cat2:
            pid = self.cat2.currentData()
            self.cat3.clear()
            self.cat3.addItem("— (nem kötelező)", None)
            if pid is not None:
                for cid, cname in _category_children(pid):
                    self.cat3.addItem(cname, cid)
            self.cat3.setCurrentIndex(0)
            self.cat4.clear()
            self.cat4.addItem("— (nem kötelező)", None)
            self.cat4.setCurrentIndex(0)
        elif sender == self.cat3:
            pid = self.cat3.currentData()
            self.cat4.clear()
            self.cat4.addItem("— (nem kötelező)", None)
            if pid is not None:
                for cid, cname in _category_children(pid):
                    self.cat4.addItem(cname, cid)
            self.cat4.setCurrentIndex(0)

    def load(self):
        self.partner.clear()
        self._partner_ids = []
        conn = db()
        try:
            c = conn.cursor()
            for r in c.execute("SELECT id,name FROM partners ORDER BY name"):
                self._partner_ids.append(r[0])
                self.partner.addItem(r[1])
            self.cat1.clear()
            self.cat1.addItem("(válasszon fő kategóriát)", None)
            for cid, cname in _category_children(None):
                self.cat1.addItem(cname, cid)
            self.cat2.clear()
            self.cat2.addItem("— (nem kötelező)", None)
            self.cat3.clear()
            self.cat3.addItem("— (nem kötelező)", None)
            self.cat4.clear()
            self.cat4.addItem("— (nem kötelező)", None)
        finally:
            conn.close()

    def pick(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Szerződés kiválasztása", "",
            "Minden fájl (*);;PDF (*.pdf);;Képek (*.png *.jpg *.jpeg *.gif *.bmp);;Word (*.doc *.docx);;Excel (*.xls *.xlsx);;Szöveg (*.txt)"
        )
        if p:
            self.file = p
            self.file_label.setText(os.path.basename(p))
            self.file_label.setStyleSheet("color: black; font-style: normal;")

    def save(self):
        self._set_field_error(self.partner, False)
        self._set_field_error(self.cat1, False)
        self._set_field_error(self.number, False)
        self._set_field_error(self.file_label, False)
        if not self.file:
            self._set_field_error(self.file_label, True)
            QMessageBox.warning(self, "Hiba", "Nincs csatolmány kiválasztva.")
            return

        is_mod = self.is_modification.isChecked()
        if is_mod:
            parent_id = self.parent_contract_combo.currentData()
            if parent_id is None:
                QMessageBox.warning(self, "Hiba", "Válassza ki, melyik eredeti szerződés módosítása ez.")
                return
            if not self.mod_number.text().strip():
                QMessageBox.warning(self, "Hiba", "A módosítás szerződésszámának megadása kötelező.")
                self.mod_number.setFocus()
                return
        else:
            pi = self.partner.currentIndex()
            cid = self.cat4.currentData() or self.cat3.currentData() or self.cat2.currentData() or self.cat1.currentData()
            if pi < 0 or not self._partner_ids:
                self._set_field_error(self.partner, True)
                QMessageBox.warning(self, "Hiba", "Nincs kiválasztott szerződő fél. (Beállítások → Szerződő felek)")
                return
            if cid is None:
                self._set_field_error(self.cat1, True)
                QMessageBox.warning(self, "Hiba", "Válasszon legalább fő kategóriát. (Beállítások → Kategóriák)")
                return
            if not self.number.text().strip():
                self._set_field_error(self.number, True)
                QMessageBox.warning(self, "Hiba", "A szerződésszám mező kitöltése kötelező.")
                return

        conn = None
        try:
            conn = db()
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if is_mod:
                parent_row = conn.execute(
                    "SELECT partner_id, category_id, contract_number, expiry_date, indefinite FROM contracts WHERE id=?",
                    (parent_id,),
                ).fetchone()
                if not parent_row:
                    QMessageBox.warning(self, "Hiba", "Az eredeti szerződés nem található.")
                    return
                partner_id, category_id, _orig_number, _exp_date, _indef = parent_row
                cur.execute("""
                INSERT INTO contracts(
                    partner_id,category_id,contract_number,
                    contract_date,expiry_date,indefinite,
                    parent_id,is_mod,updated_at,nickname
                ) VALUES(?,?,?,?,?,?,?,1,?,?)
                """, (
                    partner_id,
                    category_id,
                    self.mod_number.text().strip(),
                    self.mod_date.date().toString("yyyy-MM-dd"),
                    None if self.mod_indef.isChecked() else self.mod_exp.date().toString("yyyy-MM-dd"),
                    1 if self.mod_indef.isChecked() else 0,
                    parent_id,
                    now,
                    (self.mod_nickname.text() or "").strip() or None,
                ))
            else:
                partner_id = self._partner_ids[pi]
                category_id = self.cat4.currentData() or self.cat3.currentData() or self.cat2.currentData() or self.cat1.currentData()
                cur.execute("""
                INSERT INTO contracts(
                    partner_id,category_id,contract_number,
                    contract_date,expiry_date,indefinite,
                    parent_id,is_mod,updated_at,nickname
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    partner_id,
                    category_id,
                    self.number.text().strip(),
                    self.date.date().toString("yyyy-MM-dd"),
                    None if self.indef.isChecked() else self.exp.date().toString("yyyy-MM-dd"),
                    1 if self.indef.isChecked() else 0,
                    None,
                    0,
                    now,
                    (self.nickname.text() or "").strip() or None,
                ))
            cid = cur.lastrowid
            contract_number = conn.execute("SELECT contract_number FROM contracts WHERE id=?", (cid,)).fetchone()[0] or ""
            folder_name = _contract_folder_name(contract_number, cid)
            contract_dir = os.path.join(FILES_DIR, folder_name)
            os.makedirs(contract_dir, exist_ok=True)
            base = os.path.basename(self.file)
            new_name = f"{uuid4().hex}_{base}"
            stored = f"{folder_name}/{new_name}"
            shutil.copy2(self.file, os.path.join(contract_dir, new_name))
            cur.execute("INSERT INTO files(contract_id,filename) VALUES(?,?)", (cid, stored))

            conn.commit()
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Mentési hiba", str(e))
        finally:
            if conn:
                conn.close()

# ================= GLOBÁLIS STÍLUS (minimális) =================

APP_STYLE = """
    QGroupBox { font-weight: bold; margin-top: 10px; }
    QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
    QLineEdit, QDateEdit { min-width: 100px; }
    QComboBox { min-width: 100px; }
    QToolTip { color: #333; background-color: #fff; border: 1px solid #999; padding: 4px 8px; font-size: 12px; }
"""

def _icon(sp_name):
    try:
        return QApplication.style().standardIcon(getattr(QStyle, sp_name))
    except (AttributeError, TypeError):
        return QIcon()

ICON_SVG_MAP = {
    "open": "open.svg",
    "attach": "upload.svg",
    "replace": "refresh.svg",
    "add": "add.svg",
    "close": "close.svg",
    "save": "save.svg",
    "list": "list.svg",
    "gear": "settings.svg",
    "trash": "trash.svg",
}

def _minimal_icon(style):
    """SVG ikon betöltése az icons mappából."""
    fname = ICON_SVG_MAP.get(style, f"{style}.svg")
    path = os.path.join(ICONS_DIR, fname)
    if os.path.isfile(path):
        return QIcon(path)
    return QIcon()

DELETE_BTN_STYLE = "background-color: #c0392b; color: white; font-weight: 500;"

def _delete_button(text):
    """Törlés gomb: piros háttér, fehér betű, minimális ikon."""
    btn = QPushButton(text)
    btn.setIcon(_minimal_icon("trash"))
    btn.setIconSize(QSize(14, 14))
    btn.setStyleSheet(DELETE_BTN_STYLE)
    return btn

def _app_icon():
    """Világoskék négyzet fehér HI felirattal (Hajózási Iroda)."""
    size = 48
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0x60, 0xA5, 0xFA))  # világoskék
    margin = 2
    p.drawRect(margin, margin, size - 2 * margin, size - 2 * margin)
    p.setPen(QColor(0xff, 0xff, 0xff))
    f = QFont()
    f.setPointSize(16)
    f.setBold(True)
    p.setFont(f)
    p.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, "HI")
    p.end()
    return QIcon(pm)

class HtmlNoteDelegate(QStyledItemDelegate):
    """Megjegyzések HTML formátumú megjelenítése."""
    def paint(self, painter, option, index):
        from PySide6.QtCore import QRectF, QSize
        from PySide6.QtWidgets import QStyle
        opt = option
        painter.save()
        doc = QTextDocument()
        content = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if content.strip().startswith("<"):
            doc.setHtml(content)
        else:
            doc.setPlainText(content)
        w = max(opt.rect.width() - 8, 100)
        doc.setTextWidth(w)
        opt.text = ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.translate(opt.rect.left() + 4, opt.rect.top() + 4)
        clip = QRectF(0, 0, w, opt.rect.height() - 8)
        doc.drawContents(painter, clip)
        painter.restore()

    def sizeHint(self, option, index):
        from PySide6.QtCore import QSize
        doc = QTextDocument()
        content = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if content.strip().startswith("<"):
            doc.setHtml(content)
        else:
            doc.setPlainText(content)
        w = max(option.rect.width() - 8, 100) if option.rect.width() > 0 else 250
        doc.setTextWidth(w)
        return QSize(w + 8, int(doc.size().height()) + 8)

class ContractDetailDialog(QDialog):
    def __init__(self, parent=None, contract_id=None):
        super().__init__(parent)
        self.contract_id = contract_id
        self.setWindowTitle("Szerződés adatkezelő")
        self.resize(560, 640)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._parent_locked_banner = QLabel(
            "Figyelem! A szerződéshez módosítást csatoltak, így a vonatkozó információk a szerződésmódosítás felületén szerkezthetőek."
        )
        self._parent_locked_banner.setStyleSheet(
            "background-color: #fef3c7; color: #92400e; padding: 12px; font-weight: bold; border: 1px solid #f59e0b;"
        )
        self._parent_locked_banner.setWordWrap(True)
        self._parent_locked_banner.setVisible(False)

        # Szerződés adatai
        info = QGroupBox("Szerződés alapadatai")
        form = QFormLayout()
        self.partner_combo = QComboBox()
        self.category_combo = QComboBox()
        self.number_edit = QLineEdit()
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.nickname_edit = QLineEdit()
        self.nickname_edit.setMaxLength(20)
        # Lejárat: nem szerkesztésben csak szöveg (label), szerkesztésben checkbox + dátum
        self.expiry_display_label = QLabel()
        self.expiry_indef_cb = QCheckBox("Határozatlan idejű szerződés")
        self.expiry_indef_cb.stateChanged.connect(self._on_expiry_indef_changed)
        self.expiry_edit = QDateEdit(QDate.currentDate())
        self.expiry_edit.setCalendarPopup(True)
        self.expiry_row_widget = QWidget()
        expiry_row = QHBoxLayout(self.expiry_row_widget)
        expiry_row.setContentsMargins(0, 0, 0, 0)
        expiry_row.addWidget(self.expiry_indef_cb)
        expiry_row.addWidget(self.expiry_edit)
        expiry_row.addStretch()
        self.expiry_container = QWidget()
        expiry_cont_layout = QVBoxLayout(self.expiry_container)
        expiry_cont_layout.setContentsMargins(0, 0, 0, 0)
        expiry_cont_layout.addWidget(self.expiry_display_label)
        expiry_cont_layout.addWidget(self.expiry_row_widget)
        form.addRow("Szerződő fél:", self.partner_combo)
        form.addRow("Szerződés kategóriája:", self.category_combo)
        form.addRow("Szerződésszám:", self.number_edit)
        form.addRow("Szerződéskötés dátuma:", self.date_edit)
        form.addRow("Megnevezés (opcionális):", self.nickname_edit)
        form.addRow("Lejárat dátuma:", self.expiry_container)
        info.setLayout(form)
        layout.addWidget(info)
        base_btn_row = QHBoxLayout()
        base_btn_row.addStretch()
        self.base_edit_btn = QPushButton("Alapadatok módosítása")
        self.base_edit_btn.clicked.connect(self._toggle_base_edit)
        base_btn_row.addWidget(self.base_edit_btn)
        layout.addLayout(base_btn_row)
        # alapértelmezett megjelenés: csak szöveg, nem szerkeszthető
        self._base_edit_mode = False
        self._apply_base_edit_style(editing=False)

        self.lbl_elerhetoseg = QLabel()
        self.lbl_elerhetoseg.setWordWrap(True)
        self.lbl_elerhetoseg.setStyleSheet("color: #4b5563;")
        elerhetoseg_grp = QGroupBox("Elérhetőség")
        eler_layout = QVBoxLayout()
        eler_layout.addWidget(self.lbl_elerhetoseg)
        elerhetoseg_grp.setLayout(eler_layout)
        layout.addWidget(elerhetoseg_grp)

        # Állapotválasztó (hátralék / rendezett)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Állapot:"))
        self.status_combo = QComboBox()
        self.status_combo.addItem("Nem meghatározott", None)
        self.status_combo.addItem("Hátralék", "hatralek")
        self.status_combo.addItem("Rendezett", "rendezett")
        self.status_combo.currentIndexChanged.connect(self._set_dirty)
        status_row.addWidget(self.status_combo)
        status_row.addStretch()
        layout.addLayout(status_row)

        # Modulok kezelése
        mod_grp = QGroupBox("Modul hozzáadása")
        mod_row = QHBoxLayout()
        mod_row.addWidget(QLabel("Modul:"))
        self.module_combo = QComboBox()
        for mid, mlabel in CONTRACT_MODULES:
            self.module_combo.addItem(mlabel, mid)
        self.add_mod_btn = QPushButton("Hozzáadás")
        self.add_mod_btn.clicked.connect(self._add_module)
        mod_row.addWidget(self.module_combo)
        mod_row.addWidget(self.add_mod_btn)
        mod_row.addStretch()
        mod_grp.setLayout(mod_row)
        layout.addWidget(mod_grp)

        # Pénzügyi adatok – csak ha „Pénzügyi adatok” modul hozzáadva
        self.finance_grp = QGroupBox("Pénzügyi adatok")
        fin_layout = QFormLayout()
        monthly_row = QHBoxLayout()
        self.monthly_fee = QLineEdit()
        self.monthly_fee.setMaximumWidth(160)
        self.monthly_fee.setPlaceholderText("")
        self.monthly_fee.textChanged.connect(self._set_dirty)
        monthly_row.addWidget(self.monthly_fee)
        monthly_row.addWidget(QLabel("Ft"))
        year = datetime.now().year
        self.monthly_fee_indexed_cb = QCheckBox(f"A {year} évre aktuális fogyasztói árindex mértékével növelve")
        self.monthly_fee_indexed_cb.stateChanged.connect(self._set_dirty)
        self.monthly_fee_indexed_cb.stateChanged.connect(self._update_rendezett_enabled)
        self.monthly_fee_indexed_cb.stateChanged.connect(self._on_monthly_fee_indexed_changed)
        self.monthly_fee.textChanged.connect(self._update_rendezett_enabled)
        monthly_row.addWidget(self.monthly_fee_indexed_cb)
        monthly_row.addStretch()
        fin_layout.addRow("Havi díjösszeg:", monthly_row)

        self.deposit_check = QCheckBox("Óvadék fizetendő")
        self.deposit_check.stateChanged.connect(self._on_deposit_check_changed)
        self.deposit_check.stateChanged.connect(self._update_rendezett_enabled)
        fin_layout.addRow(self.deposit_check)

        self.deposit_row_widget = QWidget()
        deposit_layout = QFormLayout()
        req_row = QHBoxLayout()
        self.deposit_required = QLineEdit()
        self.deposit_required.setMaximumWidth(160)
        self.deposit_required.textChanged.connect(self._set_dirty)
        self.deposit_required.textChanged.connect(self._update_rendezett_enabled)
        req_row.addWidget(self.deposit_required)
        req_row.addWidget(QLabel("Ft"))
        req_row.addStretch()
        deposit_layout.addRow("Befizetendő óvadék összege:", req_row)
        amt_row = QHBoxLayout()
        self.deposit_amount = QLineEdit()
        self.deposit_amount.setMaximumWidth(160)
        self.deposit_amount.textChanged.connect(self._set_dirty)
        self.deposit_amount.textChanged.connect(self._update_rendezett_enabled)
        amt_row.addWidget(self.deposit_amount)
        amt_row.addWidget(QLabel("Ft"))
        amt_row.addStretch()
        deposit_layout.addRow("Befizetett óvadék összege:", amt_row)
        self.deposit_row_widget.setLayout(deposit_layout)
        fin_layout.addRow(self.deposit_row_widget)

        remove_fin_row = QHBoxLayout()
        remove_fin_row.addStretch()
        self.finance_lock_btn = QPushButton("Adatok rögzítése")
        self.finance_lock_btn.setMaximumWidth(120)
        self.finance_lock_btn.clicked.connect(self._toggle_finance_lock)
        remove_fin_row.addWidget(self.finance_lock_btn)
        self.remove_fin_btn = _delete_button("Modul törlése")
        self.remove_fin_btn.setMaximumWidth(100)
        self.remove_fin_btn.clicked.connect(lambda: self._remove_module("penzügyi"))
        remove_fin_row.addWidget(self.remove_fin_btn)
        fin_layout.addRow("", remove_fin_row)
        self.finance_grp.setLayout(fin_layout)
        self.finance_grp.setVisible(False)
        layout.addWidget(self.finance_grp)

        # Média modul (fájl feltöltés/letöltés)
        self.muszaki_grp = QGroupBox("Média")
        muszaki_layout = QVBoxLayout()
        muszaki_layout.addWidget(QLabel("Fájlok:"))
        self.muszaki_files_list = QListWidget()
        self.muszaki_files_list.setMinimumHeight(80)
        self.muszaki_files_list.setMaximumHeight(140)
        muszaki_btn_row = QHBoxLayout()
        self.muszaki_add_btn = QPushButton("Feltöltés")
        self.muszaki_add_btn.setIcon(_minimal_icon("upload"))
        self.muszaki_add_btn.setIconSize(QSize(14, 14))
        self.muszaki_add_btn.clicked.connect(self._muszaki_upload)
        self.muszaki_open_btn = QPushButton("Megnyitás")
        self.muszaki_open_btn.setIcon(_minimal_icon("open"))
        self.muszaki_open_btn.setIconSize(QSize(14, 14))
        self.muszaki_open_btn.clicked.connect(self._muszaki_open_file)
        self.muszaki_download_btn = QPushButton("Letöltés")
        self.muszaki_download_btn.setIcon(_minimal_icon("download"))
        self.muszaki_download_btn.setIconSize(QSize(14, 14))
        self.muszaki_download_btn.clicked.connect(self._muszaki_download_file)
        self.muszaki_del_btn = _delete_button("Törlés")
        self.muszaki_del_btn.clicked.connect(self._muszaki_remove_selected)
        muszaki_btn_row.addWidget(self.muszaki_add_btn)
        muszaki_btn_row.addWidget(self.muszaki_open_btn)
        muszaki_btn_row.addWidget(self.muszaki_download_btn)
        muszaki_btn_row.addWidget(self.muszaki_del_btn)
        muszaki_btn_row.addStretch()
        muszaki_layout.addWidget(self.muszaki_files_list)
        muszaki_layout.addLayout(muszaki_btn_row)
        muszaki_remove_row = QHBoxLayout()
        muszaki_remove_row.addStretch()
        muszaki_remove_btn = _delete_button("Modul törlése")
        muszaki_remove_btn.setMaximumWidth(100)
        muszaki_remove_btn.clicked.connect(lambda: self._remove_module("muszaki"))
        muszaki_remove_row.addWidget(muszaki_remove_btn)
        muszaki_layout.addLayout(muszaki_remove_row)
        self.muszaki_grp.setLayout(muszaki_layout)
        self.muszaki_grp.setVisible(False)
        layout.addWidget(self.muszaki_grp)

        # Szerződés (dokumentum)
        file_grp = QGroupBox("Szerződés")
        file_layout = QVBoxLayout()
        file_row = QHBoxLayout()
        self.file_name_label = QLabel("—")
        self.file_name_label.setStyleSheet("color: #888;")
        file_row.addWidget(self.file_name_label, 1)
        self.open_file_btn = QPushButton("Csatolmány\nmegnyitása")
        self.open_file_btn.setIcon(_minimal_icon("open"))
        self.open_file_btn.setIconSize(QSize(14, 14))
        self.open_file_btn.setMinimumWidth(100)
        self.open_file_btn.setMinimumHeight(44)
        self.open_file_btn.clicked.connect(self.open_file)
        self.attach_file_btn = QPushButton("Csatolmány\ncsatolása")
        self.attach_file_btn.setIcon(_minimal_icon("attach"))
        self.attach_file_btn.setIconSize(QSize(14, 14))
        self.attach_file_btn.setMinimumWidth(100)
        self.attach_file_btn.setMinimumHeight(44)
        self.attach_file_btn.clicked.connect(self.attach_file)
        self.replace_file_btn = QPushButton("Csatolmány\ncseréje")
        self.replace_file_btn.setIcon(_minimal_icon("replace"))
        self.replace_file_btn.setIconSize(QSize(14, 14))
        self.replace_file_btn.setMinimumWidth(100)
        self.replace_file_btn.setMinimumHeight(44)
        self.replace_file_btn.clicked.connect(self.replace_file)
        file_row.addWidget(self.open_file_btn)
        file_row.addWidget(self.attach_file_btn)
        file_row.addWidget(self.replace_file_btn)
        file_layout.addLayout(file_row)
        file_grp.setLayout(file_layout)
        layout.addWidget(file_grp)

        # Megjegyzések
        notes_group = QGroupBox("Megjegyzések")
        notes_layout = QVBoxLayout()
        self.notes_list = QListWidget()
        self.notes_list.setMinimumHeight(160)
        self.notes_list.setItemDelegate(HtmlNoteDelegate(self.notes_list))
        notes_layout.addWidget(self.notes_list)

        note_row = QHBoxLayout()
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Új megjegyzés...")
        self.note_input.setMaxLength(200)
        self.add_note_btn = QPushButton(" Hozzáadás")
        self.add_note_btn.setIcon(_minimal_icon("add"))
        self.add_note_btn.setIconSize(QSize(14, 14))
        self.add_note_btn.setMaximumWidth(130)
        self.add_note_btn.clicked.connect(self.add_note)
        note_row.addWidget(self.note_input)
        note_row.addWidget(self.add_note_btn)
        notes_layout.addLayout(note_row)
        notes_group.setLayout(notes_layout)
        layout.addWidget(notes_group)

        # Mentés és kilépés / Kilépés
        btn_close_row = QHBoxLayout()
        btn_close_row.setContentsMargins(0, 12, 16, 16)
        btn_close_row.addStretch()
        self._save_exit_btn = QPushButton(" Mentés és kilépés")
        self._save_exit_btn.setIcon(_minimal_icon("save"))
        self._save_exit_btn.setIconSize(QSize(14, 14))
        self._save_exit_btn.setMaximumWidth(150)
        self._save_exit_btn.clicked.connect(self._save_and_exit)
        self._exit_btn = QPushButton(" Kilépés")
        self._exit_btn.setIcon(_minimal_icon("close"))
        self._exit_btn.setIconSize(QSize(14, 14))
        self._exit_btn.setMaximumWidth(100)
        self._exit_btn.clicked.connect(self._exit_maybe_discard)
        btn_close_row.addWidget(self._save_exit_btn)
        btn_close_row.addWidget(self._exit_btn)

        # Scroll területen belül, ne lógjon ki a képernyőből
        self._content_widget = QWidget()
        self._content_widget.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._content_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._parent_locked_banner)
        main_layout.addWidget(scroll)
        main_layout.addLayout(btn_close_row)
        self.setLayout(main_layout)
        self._dirty = False
        self.load_contract()
        self.load_modules()
        self.load_notes()
        # Maximális magasság = képernyő elérhető területe
        screen_h = QApplication.primaryScreen().availableGeometry().height()
        self.setMaximumHeight(int(screen_h * 0.92))

    def _set_dirty(self):
        self._dirty = True

    def _on_deposit_check_changed(self):
        show = self.deposit_check.isChecked()
        self.deposit_row_widget.setVisible(show)
        self._dirty = True

    def _on_monthly_fee_indexed_changed(self):
        """Ha árindexre pipálva van, a havi díj mező zárolva."""
        if not getattr(self, "_finance_locked", False):
            self.monthly_fee.setReadOnly(self.monthly_fee_indexed_cb.isChecked())

    def _apply_finance_lock(self, locked):
        """Pénzügyi összegek zárolása: csak szövegként jelenjenek meg, vagy szerkeszthető mezők."""
        self._finance_locked = locked
        text_line_style = "QLineEdit { border: none; background: transparent; padding: 0; color: #111827; }"
        normal = ""
        if locked:
            # csak szövegként látható, nem szerkeszthető
            self.monthly_fee.setReadOnly(True)
            self.monthly_fee.setStyleSheet(text_line_style)
            self.deposit_required.setReadOnly(True)
            self.deposit_required.setStyleSheet(text_line_style)
            self.deposit_amount.setReadOnly(True)
            self.deposit_amount.setStyleSheet(text_line_style)
            self.deposit_check.setEnabled(False)
            self.deposit_check.setStyleSheet("")
            self.monthly_fee_indexed_cb.setEnabled(False)
            self.monthly_fee_indexed_cb.setStyleSheet("")
        else:
            # szerkeszthető mezők, normál kinézet
            self.monthly_fee.setReadOnly(self.monthly_fee_indexed_cb.isChecked())
            self.monthly_fee.setStyleSheet(normal)
            self.deposit_required.setReadOnly(False)
            self.deposit_required.setStyleSheet(normal)
            self.deposit_amount.setReadOnly(False)
            self.deposit_amount.setStyleSheet(normal)
            self.deposit_check.setEnabled(True)
            self.deposit_check.setStyleSheet(normal)
            self.monthly_fee_indexed_cb.setEnabled(True)
            self.monthly_fee_indexed_cb.setStyleSheet(normal)
        self.deposit_row_widget.setVisible(self.deposit_check.isChecked())
        self.finance_lock_btn.setText("Módosítás" if locked else "Adatok rögzítése")

    def _toggle_finance_lock(self):
        """Adatok rögzítése: zárolás. Módosítás: feloldás."""
        if getattr(self, "_finance_locked", False):
            conn = db()
            try:
                conn.execute("UPDATE contracts SET penzügyi_locked=0 WHERE id=?", (self.contract_id,))
                conn.commit()
            finally:
                conn.close()
            self._apply_finance_lock(False)
        else:
            self._save_all()
            conn = db()
            try:
                conn.execute("UPDATE contracts SET penzügyi_locked=1 WHERE id=?", (self.contract_id,))
                conn.commit()
            finally:
                conn.close()
            self._apply_finance_lock(True)
        self._dirty = False

    def _update_rendezett_enabled(self):
        """Rendezett elérhető: havidíj és óvadék feltételek szerint."""
        mf = self._parse_int(self.monthly_fee.text())
        has_monthly_fee = mf is not None and mf > 0
        indexed_this_year = self.monthly_fee_indexed_cb.isChecked()
        monthly_fee_ok = (not has_monthly_fee) or indexed_this_year
        # Óvadék: ha be van pipálva, csak akkor Rendezett, ha összeg nem nulla és befizetendő == befizetett
        dr = self._parse_int(self.deposit_required.text()) if self.deposit_check.isChecked() else None
        da = self._parse_int(self.deposit_amount.text()) if self.deposit_check.isChecked() else None
        if self.deposit_check.isChecked():
            deposit_ok = dr is not None and da is not None and dr > 0 and dr == da
        else:
            deposit_ok = True
        rendezett_ok = monthly_fee_ok and deposit_ok
        rendezett_idx = self.status_combo.findData("rendezett")
        if rendezett_idx >= 0:
            self.status_combo.model().item(rendezett_idx).setEnabled(rendezett_ok)
            # Ha most Rendezett van kiválasztva és nem megengedett, váltsunk nem meghatározottra
            if not rendezett_ok and self.status_combo.currentData() == "rendezett":
                self.status_combo.blockSignals(True)
                self.status_combo.setCurrentIndex(self.status_combo.findData(None))
                self.status_combo.blockSignals(False)

    def _save_all(self):
        """Pénzügyi mezők és állapot mentése az adatbázisba (alapadatok külön mentendők)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = db()
        try:
            mf = self._parse_int(self.monthly_fee.text())
            dr = self._parse_int(self.deposit_required.text()) if self.deposit_check.isChecked() else None
            da = self._parse_int(self.deposit_amount.text()) if self.deposit_check.isChecked() else None
            status = self.status_combo.currentData()
            cur_year = datetime.now().year
            indexed_year = cur_year if self.monthly_fee_indexed_cb.isChecked() else None
            conn.execute(
                "UPDATE contracts SET requires_deposit=?, deposit_required=?, deposit_amount=?, monthly_fee=?, monthly_fee_indexed_year=?, deposit_status=?, updated_at=? WHERE id=?",
                (1 if self.deposit_check.isChecked() else 0, dr, da, mf, indexed_year, status, now, self.contract_id),
            )
            conn.commit()
        finally:
            conn.close()
        enabled = _contract_enabled_modules(self.contract_id)
        self._dirty = False

    def _save_and_exit(self):
        self._save_all()
        # Megjegyzés: ki módosította legutoljára
        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        username = _current_username()
        note_text = f"Mentve. Módosította: {username}" if username else "Mentve."
        conn = db()
        try:
            conn.execute(
                "INSERT INTO contract_notes(contract_id, note_text, created_at, created_by) VALUES(?, ?, ?, ?)",
                (self.contract_id, note_text, created, username),
            )
            conn.commit()
        finally:
            conn.close()
        self.accept()

    def _exit_maybe_discard(self):
        if not self._dirty:
            self.accept()
            return
        if _question_igen_nem(
            self, "Kilépés",
            "Biztosan ki szeretne lépni mentés nélkül? A módosítások nem kerülnek rögzítésre.",
        ):
            self.accept()

    def _toggle_base_edit(self):
        """Alapadatok (partner, kategória, szám, dátum, megnevezés) szerkesztési mód ki/be kapcsolása."""
        if not getattr(self, "_base_edit_mode", False):
            # Szerkesztési mód bekapcsolása
            self._base_edit_mode = True
            self._apply_base_edit_style(editing=True)
        else:
            # Mentés és vissza kijelzés módba
            self._save_base_data()

    def _apply_base_edit_style(self, editing: bool):
        """Alapadat mezők megjelenítése: csak szöveg (nem szerkeszthető) vs. szerkesztőmezők."""
        if editing:
            self.base_edit_btn.setText("Mentés")
            self.partner_combo.setEnabled(True)
            self.category_combo.setEnabled(True)
            self.number_edit.setReadOnly(False)
            self.date_edit.setEnabled(True)
            self.nickname_edit.setReadOnly(False)
            # vissza az alap stílusokra (kerettel)
            self.partner_combo.setStyleSheet("")
            self.category_combo.setStyleSheet("")
            self.number_edit.setStyleSheet("")
            self.date_edit.setStyleSheet("")
            self.nickname_edit.setStyleSheet("")
            # Szerkesztésben: lejáratnál checkbox + dátum mező; dátum csak ha nincs pipálva
            self.expiry_display_label.hide()
            self.expiry_row_widget.show()
            self.expiry_edit.setEnabled(not self.expiry_indef_cb.isChecked())
        else:
            self.base_edit_btn.setText("Alapadatok módosítása")
            self.partner_combo.setEnabled(False)
            self.category_combo.setEnabled(False)
            self.number_edit.setReadOnly(True)
            self.date_edit.setEnabled(False)
            self.nickname_edit.setReadOnly(True)
            text_combo_style = (
                "QComboBox { border: none; background: transparent; padding-left: 0px; color: #111827; }"
                "QComboBox::drop-down { width: 0px; }"
            )
            self.partner_combo.setStyleSheet(text_combo_style)
            self.category_combo.setStyleSheet(text_combo_style)
            text_line_style = "QLineEdit { border: none; background: transparent; padding: 0; color: #111827; }"
            self.number_edit.setStyleSheet(text_line_style)
            self.nickname_edit.setStyleSheet(text_line_style)
            self.date_edit.setStyleSheet(
                "QDateEdit { border: none; background: transparent; padding: 0; color: #111827; }"
                "QDateEdit::drop-down { width: 0px; }"
            )
            # Lejárat: nem szerkesztésben csak a szöveg label látszik, checkbox+mező rejtve
            self.expiry_display_label.setText(
                "Határozatlan idejű" if self.expiry_indef_cb.isChecked()
                else self.expiry_edit.date().toString("yyyy-MM-dd")
            )
            self.expiry_display_label.show()
            self.expiry_row_widget.hide()

    def _on_expiry_indef_changed(self):
        """Határozatlan idejű pipa: ha be van pipálva, a lejárati dátum mező ne legyen szerkeszthető."""
        self.expiry_edit.setEnabled(not self.expiry_indef_cb.isChecked())

    def _save_base_data(self):
        partner_id = self.partner_combo.currentData()
        category_id = self.category_combo.currentData()
        contract_number = (self.number_edit.text() or "").strip()
        nickname = (self.nickname_edit.text() or "").strip() or None
        if partner_id is None:
            QMessageBox.warning(self, "Hiba", "Válasszon szerződő felet az alapadatok mentéséhez.")
            return
        if category_id is None:
            QMessageBox.warning(self, "Hiba", "Válasszon kategóriát az alapadatok mentéséhez.")
            return
        if not contract_number:
            QMessageBox.warning(self, "Hiba", "A szerződésszám nem lehet üres az alapadatok mentésekor.")
            return
        contract_date = self.date_edit.date().toString("yyyy-MM-dd")
        if self.expiry_indef_cb.isChecked():
            expiry_date = None
            indefinite = 1
        else:
            expiry_date = self.expiry_edit.date().toString("yyyy-MM-dd")
            indefinite = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = db()
        try:
            conn.execute(
                "UPDATE contracts SET partner_id=?, category_id=?, contract_number=?, contract_date=?, expiry_date=?, indefinite=?, nickname=?, updated_at=? WHERE id=?",
                (partner_id, category_id, contract_number, contract_date, expiry_date, indefinite, nickname, now, self.contract_id),
            )
            conn.commit()
        finally:
            conn.close()
        # Zárolás vissza
        self._base_edit_mode = False
        self._apply_base_edit_style(editing=False)
        self._dirty = False

    def _parse_int(self, text):
        s = (text or "").strip().replace(" ", "")
        if not s:
            return None
        try:
            return int(s) if "." not in s else int(float(s))
        except (ValueError, TypeError):
            return None

    def load_contract(self):
        self._filename = None
        conn = db()
        try:
            cur_year = datetime.now().year
            r = conn.execute("""
                SELECT p.name, c.category_id, c.contract_number, c.contract_date, c.expiry_date, c.indefinite,
                       COALESCE(c.requires_deposit, 0), c.deposit_required, c.deposit_amount, c.monthly_fee, c.partner_id, c.deposit_status,
                       c.monthly_fee_indexed_year, COALESCE(c.penzügyi_locked, 0), c.nickname
                FROM contracts c
                JOIN partners p ON p.id = c.partner_id
                WHERE c.id = ?
            """, (self.contract_id,)).fetchone()
            if r:
                # partner és kategória combók feltöltése egyszer
                if self.partner_combo.count() == 0:
                    c2 = conn.cursor()
                    for prow in c2.execute("SELECT id, name FROM partners ORDER BY name"):
                        self.partner_combo.addItem(prow[1], prow[0])
                if self.category_combo.count() == 0:
                    self.category_combo.addItem("(válasszon kategóriát)", None)
                    for cid, label in _all_categories_for_combo():
                        self.category_combo.addItem(label, cid)
                partner_id = r[10]
                cat_id = r[1]
                idx_p = self.partner_combo.findData(partner_id)
                self.partner_combo.setCurrentIndex(max(0, idx_p))
                idx_c = self.category_combo.findData(cat_id)
                self.category_combo.setCurrentIndex(max(0, idx_c))

                self.number_edit.setText(r[2] or "")
                # szerződéskötés dátuma
                date_str = r[3] or ""
                if date_str:
                    qd = QDate.fromString(date_str, "yyyy-MM-dd")
                    if qd.isValid():
                        self.date_edit.setDate(qd)
                # lejárat / határozatlan
                is_indef = bool(r[5])
                self.expiry_indef_cb.setChecked(is_indef)
                if not is_indef:
                    exp_str = r[4] or ""
                    if exp_str:
                        qd2 = QDate.fromString(exp_str, "yyyy-MM-dd")
                        if qd2.isValid():
                            self.expiry_edit.setDate(qd2)
                self.expiry_edit.setEnabled(not is_indef)
                self.expiry_display_label.setText("Határozatlan idejű" if is_indef else (r[4] or ""))
                self.nickname_edit.setText(r[14] or "")
                self.deposit_check.blockSignals(True)
                self.deposit_check.setChecked(bool(r[6]))
                self.deposit_check.blockSignals(False)
                self.deposit_required.blockSignals(True)
                v7 = int(float(r[7])) if r[7] is not None else None
                v8 = int(float(r[8])) if r[8] is not None else None
                v9 = int(float(r[9])) if r[9] is not None else None
                self.deposit_required.setText(str(v7) if v7 else "")
                self.deposit_required.blockSignals(False)
                self.deposit_amount.blockSignals(True)
                self.deposit_amount.setText(str(v8) if v8 else "")
                self.deposit_amount.blockSignals(False)
                self.monthly_fee.blockSignals(True)
                self.monthly_fee.setText(str(v9) if v9 else "")
                self.monthly_fee.blockSignals(False)
                self.monthly_fee_indexed_cb.blockSignals(True)
                # Csak aktuális évre pipálva: ha új év van, nullázódik
                indexed_year_val = r[12]
                self.monthly_fee_indexed_cb.setChecked(indexed_year_val == cur_year)
                self.monthly_fee_indexed_cb.blockSignals(False)
                self.deposit_row_widget.setVisible(bool(r[6]))
                finance_locked = bool(r[13])
                self._apply_finance_lock(finance_locked)
                if not finance_locked:
                    self.monthly_fee.setReadOnly(indexed_year_val == cur_year)
                partner_id = r[10]
                self._update_rendezett_enabled()
                self.status_combo.blockSignals(True)
                ds = (r[11] or "").strip().lower()
                # Ha rendezett, de havidíj van és árindex nincs aktuális évre, ne lehessen rendezett – váltsunk nem meghatározottra
                mf_val = int(float(r[9])) if r[9] is not None else None
                has_mf = mf_val is not None and mf_val > 0
                if ds == "rendezett" and has_mf and indexed_year_val != cur_year:
                    ds = ""
                # Ha rendezett, de óvadék be van pipálva és befizetendő != befizetett vagy nulla, váltsunk nem meghatározottra
                if ds == "rendezett" and r[6]:
                    v7, v8 = r[7], r[8]
                    if v7 is None or v8 is None or (int(float(v7)) if v7 else 0) == 0 or (int(float(v7)) if v7 else 0) != (int(float(v8)) if v8 else 0):
                        ds = ""
                if ds == "rendezett":
                    idx = self.status_combo.findData("rendezett")
                elif ds == "hatralek":
                    idx = self.status_combo.findData("hatralek")
                else:
                    idx = self.status_combo.findData(None)
                self.status_combo.setCurrentIndex(max(0, idx))
                self.status_combo.blockSignals(False)
                lines = []
                for row in _partner_contacts(partner_id or 0):
                    ctype, label, val = row
                    lines.append(f"{label}: {val}" if label else val)
                self.lbl_elerhetoseg.setText("\n".join(lines) if lines else "Nincs megadva elérhetőség.")
            else:
                self.lbl_elerhetoseg.setText("Nincs megadva elérhetőség.")
            f = conn.execute("SELECT filename FROM files WHERE contract_id = ?", (self.contract_id,)).fetchone()
            self._filename = f[0] if f else None
        finally:
            conn.close()
        path, found = resolve_contract_file(self.contract_id, self._filename)
        self._refresh_file_ui(found)

        # Zárolás: eredeti szerződéshez van módosítás, vagy ez módosítás de nem a legfrissebb
        is_orig_with_mods = not _contract_is_modification(self.contract_id) and _contract_has_modifications(self.contract_id)
        is_older_mod = _contract_is_modification(self.contract_id) and not _modification_is_latest(self.contract_id)
        # pénzügyi zár alapértelmezett állapotának megőrzése
        self._finance_db_locked = finance_locked
        warning_text = (
            "A szerződéshez új módosítást rögzítettek. Csak a legújabb módosítás felülete szerkeszthető."
        )
        if is_orig_with_mods:
            self._parent_locked_banner.setText(warning_text)
            self._parent_locked_banner.setVisible(True)
            self._apply_overall_lock(True)
            self._save_exit_btn.setEnabled(False)
        elif is_older_mod:
            self._parent_locked_banner.setText(warning_text)
            self._parent_locked_banner.setVisible(True)
            self._apply_overall_lock(True)
            self._save_exit_btn.setEnabled(False)
        else:
            self._parent_locked_banner.setVisible(False)
            self._apply_overall_lock(False)
            self._save_exit_btn.setEnabled(True)

    def _apply_overall_lock(self, locked: bool) -> None:
        """
        Teljes szerződés zárolása, ha van frissebb módosítás:
        - minden adat csak olvasható,
        - de a csatolt dokumentumok / média fájlok megnyithatók maradnak.
        """
        # alapadatok gomb letiltása, ha zárolt
        self.base_edit_btn.setEnabled(not locked)
        # állapot, modulok, megjegyzés hozzáadás tiltása
        self.status_combo.setEnabled(not locked)
        self.module_combo.setEnabled(not locked)
        self.add_mod_btn.setEnabled(not locked)
        self.remove_fin_btn.setEnabled(not locked)
        # pénzügyi blokk: mindig zárolt nézet, ha locked, különben DB szerinti állapot
        if locked:
            self.finance_lock_btn.setEnabled(False)
            self._apply_finance_lock(True)
        else:
            self.finance_lock_btn.setEnabled(True)
            self._apply_finance_lock(getattr(self, "_finance_db_locked", False))
        # média modul: csak megnyitás/letöltés engedélyezett zárolt állapotban
        if locked:
            self.muszaki_add_btn.setEnabled(False)
            self.muszaki_del_btn.setEnabled(False)
        else:
            self.muszaki_add_btn.setEnabled(True)
            self.muszaki_del_btn.setEnabled(True)
        # csatolt szerződés fájl: csak megnyitás engedélyezett zárolt állapotban
        if locked:
            self.attach_file_btn.setEnabled(False)
            self.replace_file_btn.setEnabled(False)
        else:
            self.attach_file_btn.setEnabled(True)
            self.replace_file_btn.setEnabled(True)
        # megjegyzés hozzáadása: zárolt állapotban ne lehessen új bejegyzést írni
        self.note_input.setEnabled(not locked)
        self.add_note_btn.setEnabled(not locked)

    def load_modules(self):
        enabled = _contract_enabled_modules(self.contract_id)
        self.finance_grp.setVisible("penzügyi" in enabled)
        self.muszaki_grp.setVisible("muszaki" in enabled)
        if "muszaki" in enabled:
            self._load_muszaki()

    def _add_module(self):
        mid = self.module_combo.currentData()
        if mid is None:
            return
        enabled = _contract_enabled_modules(self.contract_id)
        if mid in enabled:
            QMessageBox.information(self, "Modul", "Ez a modul már hozzá van adva.")
            return
        _add_contract_module(self.contract_id, mid)
        self.load_modules()

    def _remove_module(self, mid):
        if not _question_igen_nem(
            self, "Modul eltávolítása",
            "A modul eltávolításával a benne megadott adatok véglegesen elvesznek.\nBiztosan folytatja?",
        ):
            return
        _remove_contract_module(self.contract_id, mid)
        if mid == "penzügyi":
            conn = db()
            try:
                conn.execute(
                    "UPDATE contracts SET requires_deposit=0, deposit_required=NULL, deposit_amount=NULL, monthly_fee=NULL, monthly_fee_indexed_year=NULL, penzügyi_locked=0 WHERE id=?",
                    (self.contract_id,),
                )
                conn.commit()
            finally:
                conn.close()
            self.deposit_check.setChecked(False)
            self.deposit_required.clear()
            self.deposit_amount.clear()
            self.status_combo.blockSignals(True)
            self.status_combo.setCurrentIndex(0)
            self.status_combo.blockSignals(False)
            self.monthly_fee.clear()
            self.monthly_fee.setReadOnly(False)
            self.monthly_fee_indexed_cb.setChecked(False)
        elif mid == "muszaki":
            _muszaki_delete_all(self.contract_id)
            self.muszaki_files_list.clear()
        self.load_modules()

    def load_notes(self):
        self.notes_list.clear()
        conn = db()
        try:
            for row in conn.execute(
                "SELECT created_at, note_text, created_by FROM contract_notes WHERE contract_id = ? ORDER BY created_at DESC",
                (self.contract_id,),
            ).fetchall():
                created_at, note_text = row[0], row[1]
                created_by = (row[2] or "").strip() if len(row) > 2 else ""
                prefix = f'<span style="color:#60A5FA;font-weight:bold;font-size:0.9em">{created_at} — {created_by} — </span>'
                if note_text.strip().startswith("<"):
                    body = note_text
                else:
                    body = html.escape(note_text)
                full_html = f"<div>{prefix}{body}</div>"
                self.notes_list.addItem(QListWidgetItem(full_html))
        finally:
            conn.close()

    def add_note(self):
        text = self.note_input.text().strip()
        if not text:
            return
        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        username = _current_username()
        conn = db()
        try:
            conn.execute(
                "INSERT INTO contract_notes(contract_id, note_text, created_at, created_by) VALUES(?, ?, ?, ?)",
                (self.contract_id, text, created, username),
            )
            conn.execute("UPDATE contracts SET updated_at=? WHERE id=?", (created, self.contract_id))
            conn.commit()
        finally:
            conn.close()
        self.note_input.clear()
        self.load_notes()

    def _refresh_file_ui(self, found):
        """Fájl címke és gombok frissítése: név megjelenítése (max 30 karakter), tooltip = teljes név."""
        if self._filename and found:
            full_name = os.path.basename(self._filename)
            self.file_name_label.setToolTip(full_name)
            display = full_name if len(full_name) <= 30 else full_name[:30] + "..."
            self.file_name_label.setText(display)
            self.file_name_label.setStyleSheet("")
            self.open_file_btn.setVisible(True)
            self.open_file_btn.setEnabled(True)
            self.attach_file_btn.setVisible(False)
            self.replace_file_btn.setVisible(True)
        else:
            self.file_name_label.setToolTip("")
            self.file_name_label.setText("Nincs csatolt dokumentum")
            self.file_name_label.setStyleSheet("color: #888;")
            self.open_file_btn.setVisible(False)
            self.attach_file_btn.setVisible(True)
            self.replace_file_btn.setVisible(False)

    def _do_attach_file(self, path):
        """Fájl másolása és DB frissítése. path = forrásfájl teljes útvonala. Csere esetén a régi fájlt törli."""
        conn = db()
        try:
            old_stored = conn.execute("SELECT filename FROM files WHERE contract_id=?", (self.contract_id,)).fetchone()
            old_stored = old_stored[0] if old_stored else None
            num_row = conn.execute("SELECT contract_number FROM contracts WHERE id=?", (self.contract_id,)).fetchone()
            contract_number = (num_row[0] or "").strip() if num_row else ""
            folder_name = _contract_folder_name(contract_number, self.contract_id)
            contract_dir = os.path.join(FILES_DIR, folder_name)
            os.makedirs(contract_dir, exist_ok=True)
            if old_stored:
                old_path = os.path.join(FILES_DIR, old_stored)
                if os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
            base = os.path.basename(path)
            new_name = f"{uuid4().hex}_{base}"
            stored = f"{folder_name}/{new_name}"
            shutil.copy2(path, os.path.join(contract_dir, new_name))
            conn.execute("DELETE FROM files WHERE contract_id=?", (self.contract_id,))
            conn.execute("INSERT INTO files(contract_id,filename) VALUES(?,?)", (self.contract_id, stored))
            conn.commit()
        finally:
            conn.close()
        self._filename = stored
        path, found = resolve_contract_file(self.contract_id, stored)
        self._refresh_file_ui(found)

    def attach_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Szerződés kiválasztása", "",
            "Minden fájl (*);;PDF (*.pdf);;Képek (*.png *.jpg *.jpeg *.gif *.bmp);;Word (*.doc *.docx);;Excel (*.xls *.xlsx);;Szöveg (*.txt)"
        )
        if p:
            self._do_attach_file(p)
            QMessageBox.information(self, "Csatolmány", "A dokumentum sikeresen csatolva.")

    def replace_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Új szerződés kiválasztása (felülírja a meglévőt)", "",
            "Minden fájl (*);;PDF (*.pdf);;Képek (*.png *.jpg *.jpeg *.gif *.bmp);;Word (*.doc *.docx);;Excel (*.xls *.xlsx);;Szöveg (*.txt)"
        )
        if p:
            self._do_attach_file(p)
            QMessageBox.information(self, "Csatolmány", "A dokumentum sikeresen cserélve.")

    def open_file(self):
        if not self._filename:
            QMessageBox.information(self, "Csatolmány", "Ehhez a szerződéshez nincs melléklet rögzítve.")
            return
        path, found = resolve_contract_file(self.contract_id, self._filename)
        if not found:
            QMessageBox.warning(self, "Hiba", "A csatolt dokumentum nem található. Ellenőrizze, hogy a files mappában van-e a szerződés melléklete.")
            return
        os.startfile(path)

    def _load_muszaki(self):
        """Média fájllista betöltése."""
        self.muszaki_files_list.clear()
        for stored_path, display_name in _muszaki_list_files(self.contract_id):
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, stored_path)
            self.muszaki_files_list.addItem(item)

    def _muszaki_upload(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Média fájl feltöltése", "",
            "Minden fájl (*);;PDF (*.pdf);;Képek (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;Dokumentumok (*.doc *.docx *.xls *.xlsx *.txt)"
        )
        if not p:
            return
        stored = _muszaki_add_file(self.contract_id, p)
        if stored:
            display = _muszaki_display_name(stored)
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, stored)
            self.muszaki_files_list.addItem(item)
        else:
            QMessageBox.warning(self, "Hiba", "A fájl feltöltése sikertelen.")

    def _muszaki_open_file(self):
        item = self.muszaki_files_list.currentItem()
        if not item:
            QMessageBox.information(self, "Média", "Válasszon ki egy fájlt a listából.")
            return
        stored_path = item.data(Qt.ItemDataRole.UserRole)
        full_path = os.path.join(FILES_DIR, stored_path)
        if os.path.isfile(full_path):
            os.startfile(full_path)
        else:
            QMessageBox.warning(self, "Hiba", "A fájl nem található.")

    def _muszaki_download_file(self):
        item = self.muszaki_files_list.currentItem()
        if not item:
            QMessageBox.information(self, "Média", "Válasszon ki egy fájlt a listából.")
            return
        stored_path = item.data(Qt.ItemDataRole.UserRole)
        full_path = os.path.join(FILES_DIR, stored_path)
        if not os.path.isfile(full_path):
            QMessageBox.warning(self, "Hiba", "A fájl nem található.")
            return
        suggested = item.text()
        path, _ = QFileDialog.getSaveFileName(
            self, "Fájl mentése másként", suggested, "Minden fájl (*)"
        )
        if path:
            try:
                shutil.copy2(full_path, path)
                QMessageBox.information(self, "Letöltés", "A fájl sikeresen mentve.")
            except OSError as e:
                QMessageBox.warning(self, "Hiba", f"A mentés sikertelen: {e}")

    def _muszaki_remove_selected(self):
        item = self.muszaki_files_list.currentItem()
        if not item:
            QMessageBox.information(self, "Média", "Válasszon ki egy fájlt a törléshez.")
            return
        stored_path = item.data(Qt.ItemDataRole.UserRole)
        _muszaki_remove_file(self.contract_id, stored_path)
        self.muszaki_files_list.takeItem(self.muszaki_files_list.row(item))

# ================= MAIN =================

class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hajózási Iroda - szerződésnyilvántartó")
        self.resize(900,600)

        main = QVBoxLayout()

        header = QWidget()
        header.setMinimumHeight(88)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 10)

        top = QHBoxLayout()
        add = QPushButton("Új szerződés rögzítése")
        add.clicked.connect(self.new)
        top.addWidget(add)
        top.addStretch()
        self.delete_btn = QPushButton()
        self.delete_btn.setIcon(_minimal_icon("trash"))
        self.delete_btn.setIconSize(QSize(18, 18))
        self.delete_btn.setToolTip("Kiválasztott elem törlése")
        self.delete_btn.setToolTipDuration(5000)
        self.delete_btn.setStyleSheet(DELETE_BTN_STYLE)
        self.delete_btn.setFixedSize(36, 36)
        self.delete_btn.clicked.connect(self.delete_contract)
        self.delete_btn.setVisible(False)
        top.addWidget(self.delete_btn)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Keresés:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Szerződő fél, kategória, szerződésszám…")
        self.search_input.setMaximumWidth(280)
        self.search_input.textChanged.connect(self.filter_tree)
        search_row.addWidget(self.search_input)
        search_row.addWidget(QLabel("Kategória:"))
        self.category_combo = QComboBox()
        self.category_combo.setMinimumWidth(200)
        self._refresh_category_combo()
        self.category_combo.currentIndexChanged.connect(self.load)
        search_row.addWidget(self.category_combo)
        report_btn = QPushButton("  Kimutatás")
        report_btn.setIcon(_minimal_icon("list"))
        report_btn.setIconSize(QSize(14, 14))
        report_btn.setMinimumHeight(32)
        report_btn.clicked.connect(self.show_reports)
        search_row.addWidget(report_btn)
        search_row.addStretch()

        header_layout.addLayout(top)
        header_layout.addLayout(search_row)
        main.addWidget(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            "Szerződő fél",
            "Megnevezés",
            "Kategória",
            "Szerződésszám",
            "Szerződéskötés dátuma",
            "Lejárat dátuma",
            "Utolsó módosítás dátuma",
            "Állapot",
        ])
        self.tree.setStyleSheet("QTreeWidget { background-color: #eef6fc; }")
        self.tree.itemDoubleClicked.connect(self.open_contract)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        main.addWidget(self.tree)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 4, 0, 0)
        bottom.addStretch()
        settings_btn = QPushButton()
        settings_btn.setIcon(_minimal_icon("gear"))
        settings_btn.setIconSize(QSize(18, 18))
        settings_btn.setToolTip("Beállítások")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setFlat(True)
        settings_btn.clicked.connect(self.settings)
        bottom.addWidget(settings_btn)
        main.addLayout(bottom)

        self.setLayout(main)

        self.load()

    def _on_selection_changed(self):
        item = self.tree.currentItem()
        self.delete_btn.setVisible(item is not None and item.data(0, Qt.UserRole) is not None)

    def _refresh_category_combo(self):
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for cid, label in _all_categories_for_combo():
            self.category_combo.addItem(label, cid)
        self.category_combo.blockSignals(False)

    def _get_selected_category_ids(self):
        cid = self.category_combo.currentData()
        if cid is None:
            return None
        return set(_category_descendant_ids(cid))

    def show_reports(self):
        """Kimutatások választó ablak."""
        d = QDialog(self)
        d.setWindowTitle("Kimutatások")
        d.setMinimumSize(360, 220)
        layout = QVBoxLayout(d)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(QLabel("Válassza ki, milyen kimutatást szeretne megjeleníteni:"))
        reports_list = QListWidget()
        reports_list.addItem("Pénzügyi kimutatás")
        reports_list.setCurrentRow(0)
        layout.addWidget(reports_list)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Megnyitás")
        cancel_btn = QPushButton("Mégse")
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        def _open_selected():
            item = reports_list.currentItem()
            if not item:
                return
            if item.text() == "Pénzügyi kimutatás":
                d.accept()
                self.show_financial_report()

        ok_btn.clicked.connect(_open_selected)
        cancel_btn.clicked.connect(d.reject)
        reports_list.itemDoubleClicked.connect(lambda _: _open_selected())
        d.exec()

    def load(self):
        self.tree.clear()
        conn = db()
        try:
            rows = conn.execute("""
            SELECT c.id,
                   p.name,
                   c.nickname,
                   c.category_id,
                   c.contract_number,
                   c.contract_date,
                   c.expiry_date,
                   c.indefinite,
                   c.updated_at,
                   c.deposit_status
            FROM contracts c
            JOIN partners p ON p.id=c.partner_id
            WHERE c.parent_id IS NULL
            ORDER BY p.name, c.contract_date DESC, c.id DESC
            """).fetchall()

            cat_ids = self._get_selected_category_ids()
            if cat_ids is not None:
                rows = [r for r in rows if (r[3] or 0) in cat_ids]

            current_year = datetime.now().year
            ROW_COLORS = ("#eef6fc", "#dcecf8")
            def set_row_bg(it, rgb):
                for col in range(8):
                    it.setBackground(col, QBrush(QColor(rgb)))
            for row_idx, r in enumerate(rows):
                exp = "Határozatlan idejű" if r[7] else (r[6] or "")
                updated_raw = r[8] if r[8] else ""
                updated = updated_raw.split()[0] if updated_raw and " " in updated_raw else updated_raw
                ds = (r[9] or "").strip().lower() if r[9] else ""
                if ds == "rendezett":
                    status_text = "Rendezett"
                elif ds == "hatralek":
                    status_text = "Hátralék"
                else:
                    status_text = "Nem meghatározott"
                mods = conn.execute(
                    """SELECT id,contract_number,contract_date,expiry_date,indefinite,updated_at,deposit_status
                       FROM contracts WHERE parent_id=? ORDER BY contract_date ASC, id ASC""",
                    (r[0],),
                ).fetchall()
                if mods:
                    status_text = "—"
                    exp = "—"
                cat_path = _category_path(r[3])
                nickname = r[2] or ""
                item = QTreeWidgetItem([r[1], nickname, cat_path, r[4], r[5], exp, updated, status_text])
                item.setData(0, Qt.UserRole, r[0])
                row_bg = ROW_COLORS[row_idx % 2]
                set_row_bg(item, row_bg)
                if exp != "—" and not r[7] and r[6]:
                    try:
                        exp_year = int(str(r[6])[:4])
                        if exp_year == current_year:
                            item.setForeground(5, QBrush(QColor("red")))
                    except (ValueError, TypeError):
                        pass
                if status_text and status_text != "—":
                    if ds == "rendezett":
                        item.setForeground(7, QBrush(QColor("#2e7d32")))
                    elif ds == "hatralek":
                        item.setForeground(7, QBrush(QColor("#c62828")))
                    else:
                        item.setForeground(7, QBrush(QColor("#e65100")))
                item.setText(7, status_text)
                self.tree.addTopLevelItem(item)

                num_mods = len(mods)
                for idx, m in enumerate(mods, 1):
                    # Csak a legutolsó módosítás mutatja a lejáratot, a régi módosításoknál "—"
                    is_latest_mod = idx == num_mods
                    mod_exp = ("Határozatlan idejű" if (len(m) > 4 and m[4]) else ((m[3] or "") if len(m) > 3 else "")) if is_latest_mod else "—"
                    mod_updated_raw = (m[5] or "") if len(m) > 5 else ""
                    mod_updated = mod_updated_raw.split()[0] if mod_updated_raw and " " in mod_updated_raw else mod_updated_raw
                    mod_ds = (m[6] or "").strip().lower() if len(m) > 6 and m[6] else ""
                    # Csak a legutolsó módosítás során jelenik meg az állapot
                    if idx == num_mods:
                        if mod_ds == "rendezett":
                            mod_status = "Rendezett"
                        elif mod_ds == "hatralek":
                            mod_status = "Hátralék"
                        else:
                            mod_status = "Nem meghatározott"
                    else:
                        mod_status = "—"
                    mod_label = f"{idx}. sz. módosítás"
                    # oszlopok: [Szerződő fél, Megnevezés, Kategória, Szám, Kötés dátuma, Lejárat, Utolsó mód., Állapot]
                    sub = QTreeWidgetItem([mod_label, "", "", m[1], m[2] or "", mod_exp, mod_updated, mod_status])
                    sub.setData(0, Qt.UserRole, m[0])
                    set_row_bg(sub, row_bg)
                    if mod_exp != "—" and is_latest_mod and len(m) > 4 and not m[4] and m[3]:
                        try:
                            mod_exp_year = int(str(m[3])[:4])
                            if mod_exp_year == current_year:
                                sub.setForeground(5, QBrush(QColor("red")))
                        except (ValueError, TypeError):
                            pass
                    if mod_status and mod_status != "—":
                        if mod_ds == "rendezett":
                            sub.setForeground(7, QBrush(QColor("#2e7d32")))
                        elif mod_ds == "hatralek":
                            sub.setForeground(7, QBrush(QColor("#c62828")))
                        else:
                            sub.setForeground(7, QBrush(QColor("#e65100")))
                    item.addChild(sub)
        finally:
            conn.close()
        self.filter_tree()
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)
        self._on_selection_changed()

    def show_financial_report(self):
        """Pénzügyi kimutatás: csak azok a szerződések, ahol a pénzügyi modul engedélyezve van."""
        d = QDialog(self)
        d.setWindowTitle("Pénzügyi kimutatás")
        d.resize(800, 500)
        layout = QVBoxLayout(d)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        info_lbl = QLabel("Azon szerződések listája, amelyekhez a Pénzügyi modul hozzá van adva.")
        info_lbl.setStyleSheet("color: #4b5563;")
        layout.addWidget(info_lbl)

        table = QTreeWidget()
        table.setHeaderLabels([
            "Szerződő fél",
            "Megnevezés",
            "Kategória",
            "Szerződésszám",
            "Havi nettő bérletidíj",
            "Árindex szerint frissítve",
            "Befizetendő óvadék",
            "Befizetett óvadék",
            "Óvadék státusz",
        ])
        table.setRootIsDecorated(False)
        table.setAlternatingRowColors(False)
        layout.addWidget(table)

        conn = db()
        try:
            cur_year = datetime.now().year
            rows = conn.execute(
                """
                SELECT c.id,
                       p.name,
                       c.nickname,
                       c.category_id,
                       c.contract_number,
                       c.monthly_fee,
                       c.monthly_fee_indexed_year,
                       c.deposit_required,
                       c.deposit_amount
                FROM contracts c
                JOIN partners p ON p.id = c.partner_id
                WHERE EXISTS (
                    SELECT 1 FROM contract_modules m
                    WHERE m.contract_id = c.id AND m.module_name = 'penzügyi'
                )
                ORDER BY p.name, c.contract_number, c.id
                """
            ).fetchall()
            ROW_COLORS = ("#eef6fc", "#dcecf8")
            sum_mf = 0
            sum_dep_req = 0
            sum_dep_paid = 0
            sum_diff = 0
            for r in rows:
                cid = r[0]
                partner = r[1]
                nickname = r[2] or ""
                cat_path = _category_path(r[3])
                number = r[4] or ""
                mf = int(float(r[5])) if r[5] is not None else None
                if mf is not None:
                    sum_mf += mf
                    mf_text = f"{mf:,}".replace(",", " ") + " Ft"
                else:
                    mf_text = ""
                idx_year = r[6]
                # árindex pipálva az aktuális évre?
                if idx_year == cur_year:
                    idx_text = f"Igen ({idx_year})"
                elif idx_year is not None:
                    idx_text = f"Igen ({idx_year})"
                else:
                    idx_text = "Nem"
                dep_req = int(float(r[7])) if r[7] is not None else None
                dep_paid = int(float(r[8])) if r[8] is not None else None
                dep_req_val = dep_req if dep_req is not None else 0
                dep_paid_val = dep_paid if dep_paid is not None else 0
                sum_dep_req += dep_req_val
                sum_dep_paid += dep_paid_val
                diff_val = dep_paid_val - dep_req_val
                sum_diff += diff_val
                dep_req_text = f"{dep_req_val:,}".replace(",", " ") + " Ft" if (dep_req is not None or dep_req_val != 0) else ""
                dep_paid_text = f"{dep_paid_val:,}".replace(",", " ") + " Ft" if (dep_paid is not None or dep_paid_val != 0) else ""
                if diff_val == 0:
                    diff_text = "Rendezett"
                else:
                    diff_text = f"{diff_val:+,}".replace(",", " ") + " Ft"

                item = QTreeWidgetItem([
                    partner,
                    nickname,
                    cat_path,
                    number,
                    mf_text,
                    idx_text,
                    dep_req_text,
                    dep_paid_text,
                    diff_text,
                ])
                item.setData(0, Qt.UserRole, cid)
                # váltakozó háttér a fő lista színeihez igazodva
                row_bg = ROW_COLORS[table.topLevelItemCount() % 2]
                for col in range(item.columnCount()):
                    item.setBackground(col, QBrush(QColor(row_bg)))
                # különbözet színezése
                diff_col = 8
                if diff_val == 0:
                    item.setForeground(diff_col, QBrush(QColor("#2e7d32")))
                elif diff_val > 0:
                    item.setForeground(diff_col, QBrush(QColor("#2e7d32")))
                else:
                    item.setForeground(diff_col, QBrush(QColor("#c62828")))
                table.addTopLevelItem(item)
        finally:
            conn.close()

        # Összegző sor (egy sor, nagyon sötétkék háttér, fehér félkövér betű)
        sum_item = QTreeWidgetItem()
        sum_item.setText(0, "Összesen:")
        sum_item.setFirstColumnSpanned(False)
        # havi díj
        sum_mf_text = f"{sum_mf:,}".replace(",", " ") + " Ft/hó" if sum_mf else "0 Ft/hó"
        sum_dep_req_text = f"{sum_dep_req:,}".replace(",", " ") + " Ft" if sum_dep_req else "0 Ft"
        sum_dep_paid_text = f"{sum_dep_paid:,}".replace(",", " ") + " Ft" if sum_dep_paid else "0 Ft"
        if sum_diff == 0:
            sum_diff_text = "Rendezett"
        else:
            sum_diff_text = f"{sum_diff:+,}".replace(",", " ") + " Ft"
        sum_item.setText(4, sum_mf_text)
        sum_item.setText(6, sum_dep_req_text)
        sum_item.setText(7, sum_dep_paid_text)
        sum_item.setText(8, sum_diff_text)
        # vizuális kiemelés: nagyon sötétkék háttér, fehér félkövér betű
        bold_font = QFont()
        bold_font.setBold(True)
        dark_blue = QColor("#0f172a")
        white = QColor("#ffffff")
        for col in range(sum_item.columnCount()):
            sum_item.setFont(col, bold_font)
            sum_item.setBackground(col, QBrush(dark_blue))
            sum_item.setForeground(col, QBrush(white))
        table.addTopLevelItem(sum_item)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Bezárás")
        close_btn.clicked.connect(d.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        d.exec()

    def filter_tree(self):
        q = (self.search_input.text() or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not q:
                item.setHidden(False)
                for j in range(item.childCount()):
                    item.child(j).setHidden(False)
                continue
            all_text = " ".join(item.text(c) for c in range(item.columnCount()))
            for j in range(item.childCount()):
                sub = item.child(j)
                all_text += " " + " ".join(sub.text(c) for c in range(sub.columnCount()))
            matches = q in all_text.lower()
            item.setHidden(not matches)
            for j in range(item.childCount()):
                item.child(j).setHidden(False)

    def new(self):
        d = NewContract(self)
        d.exec()
        self.load()
        self.tree.viewport().update()
        QApplication.processEvents()

    def open_contract(self, item, column):
        cid = item.data(0, Qt.UserRole)
        if cid is None:
            return
        d = ContractDetailDialog(self, cid)
        d.exec()
        self.load()

    def delete_contract(self):
        item = self.tree.currentItem()
        if not item:
            QMessageBox.information(self, "Törlés", "Válasszon ki egy szerződést a listából.")
            return
        cid = item.data(0, Qt.UserRole)
        if cid is None:
            return
        # Jelszó bekérése törléshez
        pwd, ok = QInputDialog.getText(
            self,
            "Törlés jelszóval",
            "Adja meg a törléshez szükséges jelszót:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or pwd != "PestBuda2026":
            QMessageBox.warning(self, "Törlés", "Hibás jelszó, a törlés nem történt meg.")
            return
        if not _question_igen_nem(
            self, "Szerződés törlése",
            "Biztosan törölni szeretné ezt a szerződést? A mellékletek is törlődnek.",
        ):
            return
        conn = db()
        folder_to_remove = None
        parent_id = None
        try:
            pr = conn.execute("SELECT parent_id FROM contracts WHERE id = ?", (cid,)).fetchone()
            if pr and pr[0] is not None:
                parent_id = pr[0]
            row = conn.execute("SELECT filename FROM files WHERE contract_id = ?", (cid,)).fetchone()
            if row and "/" in row[0]:
                folder_to_remove = row[0].split("/")[0]
            conn.execute("DELETE FROM files WHERE contract_id = ?", (cid,))
            conn.execute("DELETE FROM contract_notes WHERE contract_id = ?", (cid,))
            conn.execute("DELETE FROM contracts WHERE id = ?", (cid,))
            conn.commit()
        finally:
            conn.close()
        if folder_to_remove:
            contract_dir = os.path.join(FILES_DIR, folder_to_remove)
            if os.path.isdir(contract_dir):
                try:
                    shutil.rmtree(contract_dir)
                except Exception:
                    pass
        self.load()
        if parent_id is not None:
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                if top.data(0, Qt.UserRole) == parent_id:
                    top.setExpanded(True)
                    if top.childCount() > 0:
                        self.tree.setCurrentItem(top.child(top.childCount() - 1))
                    else:
                        self.tree.setCurrentItem(top)
                    break

    def settings(self):
        d = QDialog(self)
        d.setWindowTitle("Beállítások")
        d.setMinimumSize(380, 280)
        layout = QVBoxLayout(d)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        grp = QGroupBox("Alapadatok")
        grp_layout = QVBoxLayout()
        grp_layout.setSpacing(12)
        b1 = QPushButton("  Szerződő felek kezelése")
        b1.setIcon(_minimal_icon("list"))
        b1.setIconSize(QSize(14, 14))
        b1.setMinimumHeight(44)
        b1.clicked.connect(lambda: ListDialog("partners").exec())
        b2 = QPushButton("  Kategóriák kezelése")
        b2.setIcon(_minimal_icon("list"))
        b2.setIconSize(QSize(14, 14))
        b2.setMinimumHeight(44)
        b2.clicked.connect(lambda: CategoryListDialog(self).exec())
        grp_layout.addWidget(b1)
        grp_layout.addWidget(b2)
        grp.setLayout(grp_layout)
        layout.addWidget(grp)
        layout.addStretch()
        close_btn = QPushButton("  Bezárás")
        close_btn.setIcon(_minimal_icon("close"))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setMinimumHeight(40)
        close_btn.setMaximumWidth(140)
        close_btn.clicked.connect(d.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        d.exec()

# ================= RUN =================

app = QApplication(sys.argv)
app.setStyleSheet(APP_STYLE)
app.setWindowIcon(_app_icon())

lock_ok, lock_fd = _acquire_app_lock()
if not lock_ok:
    QMessageBox.warning(
        None,
        "Hajózási Iroda - szerződésnyilvántartó",
        "A szerződésnyilvántartót éppen egy másik felhasználó használja. A nyilvántartóból az adatvesztés elkerülése miatt egyszerre csak egy példány futhat.",
    )
    sys.exit(1)

atexit.register(lambda: _release_app_lock(lock_fd))

# Egyszerű töltőképernyő a betöltés idejére
loading = QDialog()
loading.setWindowTitle("Szerződés nyilvántartó")
loading.setWindowFlags(
    Qt.WindowType.Dialog
    | Qt.WindowType.CustomizeWindowHint
    | Qt.WindowType.WindowTitleHint
)
loading.setModal(True)
v = QVBoxLayout(loading)
v.setContentsMargins(24, 24, 24, 24)
v.setSpacing(16)
label = QLabel("Szerződés nyilvántartás adatainak betöltése…")
label.setAlignment(Qt.AlignmentFlag.AlignCenter)
spinner = QProgressBar()
spinner.setRange(0, 0)  # végtelen „körbefutó” mód
v.addWidget(label)
v.addWidget(spinner)
loading.setLayout(v)
loading.resize(420, 140)
loading.show()
app.processEvents()

_main_window = None

def _start_main():
    global _main_window
    init_db()
    _main_window = Main()
    _main_window.setWindowIcon(_app_icon())
    _main_window.show()
    loading.accept()

QTimer.singleShot(0, _start_main)

sys.exit(app.exec())