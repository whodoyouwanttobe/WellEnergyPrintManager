"""Окно «Напечатать в WPM» — открывается из меню Проводника (правый клик по папке,
по одному или нескольким PDF, в том числе из разных папок).

• Сверху — очередь выделенного: каждая папка или группа PDF отдельной строкой с состоянием
  анализа. Пока окно открыто, можно выделить в Проводнике ещё файлы — они допишутся сюда.
• Ниже — форматы по всему выделенному (сколько листов осталось), копии, «Печать».
• «Печать» ставит всё в КОНЕЦ очереди печати (уже идущая печать не прерывается)
  и открывает главное окно с ходом печати.

Данные анализа окно хранит само (self.data / self.printed) — главное окно с открытой
папкой при этом не трогается.
"""
import os
import tkinter as tk

import customtkinter as ctk

from core.job_sources import same_source
from core.quick_plan import format_summary

NORMAL = ("gray10", "#DCE4EE")


class QuickPrintDialog(tk.Toplevel):
    def __init__(self, app, on_print=None, on_cancel=None):
        super().__init__(app)
        self.app = app
        self.on_print = on_print
        self.on_cancel = on_cancel
        self.sources = []          # [{"folder", "files", "title", "state", "count"}]
        self.data = {}             # fp -> {"pages": [...]}
        self.printed = {}          # fp -> {page: {"printed": n}}
        self.format_vars = {}
        self._row_labels = {}
        self._total = 0
        self.skipped = 0
        self.title("Напечатать в WPM")
        self.geometry("560x600")
        self.minsize(480, 480)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())

        root = ctk.CTkFrame(self, corner_radius=0)
        root.pack(fill="both", expand=True)
        ctk.CTkLabel(root, text="🖨 Напечатать в WPM", font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        self.busy_lbl = ctk.CTkLabel(root, text="", text_color="#5dade2", anchor="w", wraplength=520, justify="left")
        self.busy_lbl.pack(fill="x", padx=16)

        self.src_frame = ctk.CTkScrollableFrame(root, height=96, label_text="Выделено (очередь)")
        self.src_frame.pack(fill="x", padx=16, pady=(6, 4))
        self.note_lbl = ctk.CTkLabel(root, text="", text_color="#f39c12", anchor="w")
        self.note_lbl.pack(fill="x", padx=16)

        self.status = ctk.CTkLabel(root, text="Анализ… определяем форматы страниц", anchor="w",
                                   text_color="#f39c12", font=("Segoe UI", 13))
        self.status.pack(fill="x", padx=16)
        self.bar = ctk.CTkProgressBar(root, mode="indeterminate", height=10)
        self.bar.pack(fill="x", padx=16, pady=(4, 6))
        self.bar.start()

        self.list_frame = ctk.CTkScrollableFrame(root, height=170, label_text="Какие форматы печатать")
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=4)

        row = ctk.CTkFrame(root, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 2))
        ctk.CTkLabel(row, text="Копий:", font=("Segoe UI", 13)).pack(side="left")
        ctk.CTkButton(row, text="−", width=32, command=lambda: self._step_copies(-1)).pack(side="left", padx=(8, 2))
        self.copies_var = ctk.StringVar(master=self, value=str(app.target_copies.get()))
        self.copies_entry = ctk.CTkEntry(row, width=50, textvariable=self.copies_var, justify="center")
        self.copies_entry.pack(side="left")
        self.copies_entry.bind("<KeyRelease>", lambda e: self.refresh())
        ctk.CTkButton(row, text="+", width=32, command=lambda: self._step_copies(1)).pack(side="left", padx=2)
        self.reprint_var = ctk.BooleanVar(master=self, value=False)
        ctk.CTkCheckBox(row, text="печатать заново и уже напечатанное", variable=self.reprint_var,
                        command=self.refresh).pack(side="left", padx=(16, 0))

        self.total = ctk.CTkLabel(root, text="", anchor="w", font=("Segoe UI", 13, "bold"), text_color="#5dade2")
        self.total.pack(fill="x", padx=16, pady=(6, 4))

        btns = ctk.CTkFrame(root, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(4, 14))
        self.btn_print = ctk.CTkButton(btns, text="🖨 Печать", height=40, font=("Segoe UI", 15, "bold"),
                                       fg_color="#2ecc71", hover_color="#27ae60", state="disabled", command=self._print)
        self.btn_print.pack(side="right")
        ctk.CTkButton(btns, text="Отмена", height=40, fg_color="#7f8c8d", hover_color="#95a5a6",
                      command=self._cancel).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._print())
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - 560) // 2}+{max((sh - 640) // 3, 0)}")

    # ───────────── очередь выделенного ─────────────
    def add_sources(self, sources, skipped=0):
        """→ индексы ДОБАВЛЕННЫХ источников (уже бывшие в окне не дублируются)."""
        added = []
        for s in sources:
            if any(same_source(s, o) and (s["files"] is None or set(s["files"]) <= set(o["files"] or []))
                   for o in self.sources):
                continue
            # те же файлы из той же папки, выделенные позже, — дописываем к существующей строке
            twin = next((o for o in self.sources if o["files"] is not None and s["files"] is not None
                         and os.path.normcase(o["folder"]) == os.path.normcase(s["folder"])), None)
            if twin is not None:
                for f in s["files"]:
                    if f not in twin["files"]:
                        twin["files"].append(f)
                n = len(twin["files"])
                twin["title"] = f"📄 {n} PDF из «{os.path.basename(twin['folder']) or twin['folder']}»"
                if twin["state"] == "done":
                    twin["state"] = "queued"     # перечитать: новые файлы нужно проанализировать
                    added.append(self.sources.index(twin))
                continue
            s = dict(s, state="queued", count=0)
            self.sources.append(s)
            added.append(len(self.sources) - 1)
        self.skipped += skipped
        self.note_lbl.configure(text=f"Пропущено — не PDF: {self.skipped} файл(ов)" if self.skipped else "")
        self._render_sources()
        self._update_status()
        return added

    def _render_sources(self):
        for w in self.src_frame.winfo_children():
            w.destroy()
        icons = {"queued": "⏳ ждёт анализа", "scanning": "🔎 анализ…", "done": "", "error": "✗ ошибка"}
        for i, s in enumerate(self.sources, start=1):
            st = icons.get(s["state"], "")
            if s["state"] == "done":
                st = f"✓ {s['count']} PDF"
            ctk.CTkLabel(self.src_frame, text=f"{i}. {s['title']}   {st}", anchor="w",
                         text_color=NORMAL if s["state"] != "error" else "#e74c3c").pack(fill="x")
        self.title(f"Напечатать в WPM — {len(self.sources)} в очереди" if len(self.sources) > 1
                   else f"Напечатать в WPM — {self.sources[0]['title'][2:] if self.sources else ''}")

    def source_progress(self, idx, done, total):
        if 0 <= idx < len(self.sources):
            self.sources[idx]["state"] = "scanning"
        if total:
            if str(self.bar.cget("mode")) != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.bar.set(done / total)
        self.status.configure(text=f"Анализ {idx + 1}/{len(self.sources)}… {done} из {total or '?'} файлов",
                              text_color="#f39c12")

    def source_done(self, idx, results, printed, error=None):
        s = self.sources[idx]
        if error:
            s["state"] = "error"
        else:
            keep = set(s["files"]) if s["files"] is not None else None
            for fp, d in results.items():
                if keep is None or fp in keep:
                    self.data[fp] = d
                    self.printed[fp] = printed.get(fp, {})
            s["count"] = len([fp for fp in results if keep is None or fp in keep])
            s["state"] = "done"
        self._render_sources()
        self._update_status()
        self.refresh(rebuild=True)

    def _update_status(self):
        pending = [s for s in self.sources if s["state"] in ("queued", "scanning")]
        if pending:
            return
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(1)
        n = len(self.files())
        self.status.configure(text=f"Готово: {n} PDF" if n else "PDF не найдены",
                              text_color="#2ecc71" if n else "#e74c3c")

    @property
    def ready(self):
        return bool(self.sources) and all(s["state"] in ("done", "error") for s in self.sources) and bool(self.files())

    def files(self):
        out = []
        for s in self.sources:
            if s["state"] != "done":
                continue
            if s["files"] is not None:
                out += [f for f in s["files"] if f in self.data and f not in out]
            else:
                pre = os.path.normcase(os.path.abspath(s["folder"]))
                out += [f for f in self.data if os.path.normcase(os.path.abspath(f)).startswith(pre) and f not in out]
        return out

    def set_busy_text(self, text):
        self.busy_lbl.configure(text=text or "")

    # ───────────── форматы и копии ─────────────
    def copies(self):
        try:
            return max(1, int(self.copies_var.get()))
        except ValueError:
            return 1

    def _step_copies(self, d):
        self.copies_var.set(str(max(1, self.copies() + d)))
        self.refresh()

    def refresh(self, rebuild=False):
        files = self.files()
        if not files:
            self.btn_print.configure(state="disabled")
            return
        summ = format_summary(self.data, self.printed, files, self.copies(), reprint=self.reprint_var.get())
        if rebuild or set(summ) != set(self.format_vars):
            for w in self.list_frame.winfo_children():
                w.destroy()
            old = {f: v.get() for f, v in self.format_vars.items()}
            self.format_vars, self._row_labels = {}, {}
            for f, d in summ.items():
                # нестандартный размер — по умолчанию выключен; шире рулона плоттера — нельзя выбрать
                default = d["sheets"] > 0 and d["standard"] and d["fits"]
                var = ctk.BooleanVar(master=self, value=old.get(f, default) and d["fits"])
                fr = ctk.CTkFrame(self.list_frame, fg_color="transparent")
                fr.pack(fill="x", pady=2)
                cb = ctk.CTkCheckBox(fr, text=f, variable=var, width=70, font=("Segoe UI", 13, "bold"),
                                     command=self.refresh)
                cb.pack(side="left")
                if not d["fits"]:
                    cb.configure(state="disabled")
                lbl = ctk.CTkLabel(fr, text="", anchor="w")
                lbl.pack(side="left", fill="x", expand=True)
                self.format_vars[f] = var
                self._row_labels[f] = lbl
        total = 0
        for f, d in summ.items():
            dest = "принтер HP" if d["laser"] else "плоттер Canon"
            left = f"осталось {d['sheets']} л." if d["sheets"] else "✓ уже напечатано"
            text, color = f"{d['pages']} стр. · {left} · {dest}", (NORMAL if d["sheets"] else "gray")
            if not d["fits"]:
                text, color = f"{d['pages']} стр. · ✗ шире рулона плоттера (914 мм) — не печатается", "#e74c3c"
                self.format_vars[f].set(False)
            elif not d["standard"]:
                text, color = f"{d['pages']} стр. · ⚠ нестандартный размер, проверьте · {left}", "#f39c12"
            self._row_labels[f].configure(text=text, text_color=color)
            if self.format_vars[f].get():
                total += d["sheets"]
        waiting = any(s["state"] in ("queued", "scanning") for s in self.sources)
        txt = f"Будет напечатано: {total} л." if total else "Нечего печатать — выберите форматы"
        if waiting:
            txt += "   (ещё идёт анализ — дождитесь)"
        self.total.configure(text=txt)
        self.btn_print.configure(state="normal" if total and not waiting else "disabled")
        self._total = total

    def selected_formats(self):
        return [f for f, v in self.format_vars.items() if v.get()]

    # ───────────── кнопки ─────────────
    def _print(self):
        if not self.ready or not self._total or any(s["state"] in ("queued", "scanning") for s in self.sources):
            return
        fmts, copies, reprint = self.selected_formats(), self.copies(), self.reprint_var.get()
        payload = {"data": dict(self.data), "printed": dict(self.printed), "files": self.files(),
                   "sources": [dict(s) for s in self.sources]}
        self.destroy()
        if self.on_print:
            self.on_print(fmts, copies, reprint, payload)

    def _cancel(self):
        self.destroy()
        if self.on_cancel:
            self.on_cancel()
