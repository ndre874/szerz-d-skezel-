import os
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess

# === ITT ADD MEG A HÁLÓZATI MAPPÁT ===
BASE_PATH = r"\\cfs01\Muszaki\Hajózási Iroda\Molnár Endre\Szerződésnyilvántartó"


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Szerződés megnyitó")
        self.root.geometry("500x250")
        self.root.resizable(False, False)

        self.partner_var = tk.StringVar()
        self.sub_var = tk.StringVar()
        self.file_var = tk.StringVar()

        ttk.Label(root, text="Partner").pack(pady=(15, 5))
        self.partner_combo = ttk.Combobox(root, textvariable=self.partner_var, state="readonly")
        self.partner_combo.pack(fill="x", padx=40)
        self.partner_combo.bind("<<ComboboxSelected>>", self.partner_selected)

        ttk.Label(root, text="Almappa").pack(pady=(10, 5))
        self.sub_combo = ttk.Combobox(root, textvariable=self.sub_var, state="readonly")
        self.sub_combo.pack(fill="x", padx=40)
        self.sub_combo.bind("<<ComboboxSelected>>", self.sub_selected)

        ttk.Label(root, text="Fájl").pack(pady=(10, 5))
        self.file_combo = ttk.Combobox(root, textvariable=self.file_var, state="readonly")
        self.file_combo.pack(fill="x", padx=40)

        ttk.Button(root, text="Megnyit", command=self.open_file).pack(pady=20)

        self.load_partners()

    def load_partners(self):
        try:
            partners = [
                f for f in os.listdir(BASE_PATH)
                if os.path.isdir(os.path.join(BASE_PATH, f))
            ]
            self.partner_combo["values"] = sorted(partners)
        except Exception as e:
            messagebox.showerror("Hiba", f"Nem érhető el a mappa:\n{e}")

    def partner_selected(self, event):
        path = os.path.join(BASE_PATH, self.partner_var.get())
        self.sub_var.set("")
        self.file_var.set("")
        self.file_combo["values"] = []

        if os.path.exists(path):
            subs = [
                f for f in os.listdir(path)
                if os.path.isdir(os.path.join(path, f))
            ]
            self.sub_combo["values"] = sorted(subs)

    def sub_selected(self, event):
        path = os.path.join(BASE_PATH, self.partner_var.get(), self.sub_var.get())
        self.file_var.set("")

        if os.path.exists(path):
            files = [
                f for f in os.listdir(path)
                if os.path.isfile(os.path.join(path, f))
            ]
            self.file_combo["values"] = sorted(files)

    def open_file(self):
        if not self.partner_var.get() or not self.sub_var.get() or not self.file_var.get():
            messagebox.showwarning("Figyelem", "Válassz ki minden mezőt!")
            return

        path = os.path.join(
            BASE_PATH,
            self.partner_var.get(),
            self.sub_var.get(),
            self.file_var.get()
        )

        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showerror("Hiba", "A fájl nem található!")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
