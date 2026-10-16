import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageTk, ImageDraw, ImageFont
import os
import time
import threading
import queue
from ui.components.dialog_utils import make_modal

try:
    import fitz
except ImportError:
    import pymupdf as fitz


# Canon roll width = 594.0mm (A1 width). Display as ROLL_DISPLAY_WIDTH pixels.
ROLL_WIDTH_MM = 594.0
ROLL_DISPLAY_WIDTH = 480  # pixels on screen for the roll
MM_TO_PX = ROLL_DISPLAY_WIDTH / ROLL_WIDTH_MM  # ~0.808 px/mm

# HP tray max display width for a page
HP_MAX_PAGE_WIDTH = 250


class PrintVisualizer(tk.Toplevel):
    """Graphical print simulation window with HP tray and Canon roll views."""

    def __init__(self, master, plan, simulate=True):
        super().__init__(master)

        self.title("Визуализация и Симуляция печати")
        self.geometry("1200x750")
        self.plan = plan
        self.simulate = simulate
        self.minsize(900, 550)
        self._close = make_modal(self, master)

        # Tk НЕ потокобезопасен: фоновый поток только рендерит PIL-картинки и кладёт
        # их в очередь, а PhotoImage и рисование на Canvas — строго в главном потоке.
        # Раньше ImageTk.PhotoImage создавался в фоне -> окно зависало/оставалось пустым.
        self._q = queue.Queue()
        self._stop = threading.Event()
        self.bind("<Destroy>", self._on_destroy, add="+")

        self.bg_frame = ctk.CTkFrame(self)
        self.bg_frame.pack(fill="both", expand=True)

        self.bg_frame.grid_rowconfigure(0, weight=1)
        self.bg_frame.grid_columnconfigure(0, weight=1)
        self.bg_frame.grid_columnconfigure(1, weight=3)

        # Left pane: Console log
        self.console = ctk.CTkTextbox(self.bg_frame, font=("Consolas", 11), width=380)
        self.console.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # Right pane: Canvas tabs
        self.tabs = ctk.CTkTabview(self.bg_frame)
        self.tabs.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        self.tabs.add("HP (Лотки A4/A3)")
        self.tabs.add("Canon (Рулон 594мм)")

        # Bottom Button Frame
        btn_frame = ctk.CTkFrame(self.bg_frame, fg_color="transparent")
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        
        ctk.CTkButton(btn_frame, text="Закрыть", fg_color="#e74c3c", hover_color="#c0392b", command=self._close).pack(side="right", padx=5)
        
        self.btn_print = ctk.CTkButton(btn_frame, text="Печать плана", fg_color="#2ecc71", hover_color="#27ae60", command=self._start_print)
        if not self.simulate:
            self.btn_print.pack(side="right", padx=5)

        # HP Canvas
        hp_tab = self.tabs.tab("HP (Лотки A4/A3)")
        self.hp_canvas = tk.Canvas(hp_tab, bg="#c8c8c8", highlightthickness=0)
        self.hp_scrollbar = _make_scrollbar(hp_tab, self.hp_canvas)
        self.hp_canvas.bind("<MouseWheel>", lambda e: self.hp_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # Canon Canvas
        cn_tab = self.tabs.tab("Canon (Рулон 594мм)")
        self.cn_canvas = tk.Canvas(cn_tab, bg="#a0a0a0", highlightthickness=0)
        self.cn_scrollbar = _make_scrollbar(cn_tab, self.cn_canvas)
        self.cn_canvas.bind("<MouseWheel>", lambda e: self.cn_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # Track Y positions for stacking pages
        self.hp_y = 15
        self.cn_y = 25
        self.hp_col_x = 15
        self.hp_row_max_h = 0

        # Keep references to PhotoImages (prevent GC)
        self.photo_images = []

        # Stats counters
        self.hp_count = 0
        self.cn_count = 0

        # Draw the roll background on Canon tab
        self._draw_roll_bg()

        has_laser = any(inf["missing"] > 0 for _, _, _, inf in self.plan.get("laser", []))
        has_plotter = any(inf["missing"] > 0 for _, _, _, inf in self.plan.get("plotter", []))

        if self.simulate:
            self.console.insert("end", "=== ЗАПУСК ТЕСТОВОГО РЕЖИМА ===\n\n")
            self._log(f"HP план: {len(self.plan.get('laser', []))} групп")
            self._log(f"Canon план: {len(self.plan.get('plotter', []))} групп\n")
            if not has_laser:
                self._log("[HP] Нет заданий для лазерного принтера.\n")
                self.after(100, lambda: self.hp_canvas.create_text(
                    200, 50, text="Нет листов A4/A3 для печати",
                    fill="#666", font=("Segoe UI", 14)))
            if not has_plotter:
                self._log("[Canon] Нет заданий для плоттера.\n")
            threading.Thread(target=self._run_simulation, daemon=True).start()
        else:
            self.console.insert("end", "Отображение текущего плана...\n")
            threading.Thread(target=self._draw_all, daemon=True).start()

        self.after(30, self._pump)

    def _on_destroy(self, event):
        if event.widget is self:
            self._stop.set()

    def _pump(self):
        """Главный поток: забираем готовые картинки из очереди и рисуем."""
        if not self.winfo_exists():
            return
        try:
            for _ in range(20):
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.console.insert("end", item[1] + "\n")
                    self.console.see("end")
                elif kind == "hp":
                    photo = ImageTk.PhotoImage(item[1], master=self)
                    self.photo_images.append(photo)
                    self._place_hp(photo, item[2])
                elif kind == "cn":
                    photo = ImageTk.PhotoImage(item[1], master=self)
                    self.photo_images.append(photo)
                    self._place_cn(photo, item[2], item[3], item[4])
        except queue.Empty:
            pass
        except tk.TclError:
            return
        self.after(30, self._pump)

    def _draw_roll_bg(self):
        """Draw initial roll background strip on Canon canvas."""
        c = self.cn_canvas
        rx = 30
        c.create_rectangle(rx, 0, rx + ROLL_DISPLAY_WIDTH, 8000,
                           fill="#f5f5f0", outline="#bbb", width=1, tags="roll_bg")
        c.create_text(rx + ROLL_DISPLAY_WIDTH // 2, 12,
                      text=f"← Рулон {ROLL_WIDTH_MM:.0f}мм →", fill="#888",
                      font=("Segoe UI", 9))
        self.cn_y = 25

    def _log(self, msg):
        self._q.put(("log", msg))

    def _render_page(self, filepath, page_num, max_w, max_h):
        """Render a PDF page scaled to fit within max_w x max_h pixels."""
        try:
            doc = fitz.open(filepath)
            page = doc.load_page(page_num - 1)
            rect = page.rect

            # Calculate scale to fit in max_w x max_h
            sx = max_w / rect.width
            sy = max_h / rect.height
            scale = min(sx, sy)
            mat = fitz.Matrix(scale, scale)

            pix = page.get_pixmap(matrix=mat, alpha=True)
            img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3]) # Use alpha as mask to remove black backgrounds
            doc.close()
            return bg
        except Exception as e:
            err_str = str(e)
            self._log(f"⚠ Рендеринг стр.{page_num}: {e}")
            pw = min(int(max_w), 200)
            ph = min(int(max_h), 140)
            img = Image.new("RGB", (pw, ph), "#ffdddd")
            draw = ImageDraw.Draw(img)
            if "compression bomb" in err_str.lower():
                draw.text((10, 10), f"Стр.{page_num}: Сложный CAD\n(На печать не повлияет)", fill="red")
            else:
                draw.text((10, 10), f"Ошибка стр.{page_num}", fill="red")
            return img

    def _start_print(self):
        """Invoke printing from the visualizer."""
        master = self.master
        self._close()
        if hasattr(master, 'print_all'):
            master.after(50, master.print_all)

    def _get_page_mm(self, filepath, page_num):
        """Get page dimensions in mm."""
        try:
            doc = fitz.open(filepath)
            page = doc.load_page(page_num - 1)
            w_mm = page.rect.width * 25.4 / 72.0
            h_mm = page.rect.height * 25.4 / 72.0
            doc.close()
            return w_mm, h_mm
        except:
            return 297, 210  # fallback A4

    # ────────────── HP (Laser) ──────────────

    def _draw_hp_page(self, filepath, page_num, fmt):
        """Draw a page on the HP canvas."""
        w_mm, h_mm = self._get_page_mm(filepath, page_num)

        # Scale to fit HP display: max width HP_MAX_PAGE_WIDTH
        aspect = h_mm / w_mm if w_mm > 0 else 1.0
        disp_w = min(w_mm * MM_TO_PX, HP_MAX_PAGE_WIDTH)
        disp_h = disp_w * aspect

        img = self._render_page(filepath, page_num, disp_w, disp_h)
        self.hp_count += 1
        self._q.put(("hp", img, fmt))

    def _place_hp(self, photo, fmt):
        c = self.hp_canvas
        margin = 12
        x = self.hp_col_x
        y = self.hp_y

        # Shadow + sheet
        c.create_rectangle(x + 3, y + 3, x + 3 + photo.width(), y + 3 + photo.height(),
                           fill="#888", outline="")
        c.create_rectangle(x, y, x + photo.width(), y + photo.height(),
                           fill="white", outline="#333", width=1)
        c.create_image(x, y, image=photo, anchor="nw")
        c.create_text(x + photo.width() // 2, y + photo.height() + 10,
                      text=fmt, fill="#333", font=("Segoe UI", 8, "bold"))

        sheet_h = photo.height() + 22
        self.hp_row_max_h = max(self.hp_row_max_h, sheet_h)
        self.hp_col_x += photo.width() + margin

        # Wrap row if too wide
        if self.hp_col_x > 450:
            self.hp_col_x = 15
            self.hp_y += self.hp_row_max_h + margin
            self.hp_row_max_h = 0

        c.configure(scrollregion=(0, 0, 550, max(self.hp_y + self.hp_row_max_h + 50, 400)))

    # ────────────── Canon (Roll) ──────────────

    def _draw_cn_page(self, filepath, page_num, fmt):
        """Draw a page on the Canon roll, scaled to fit within roll width."""
        w_mm, h_mm = self._get_page_mm(filepath, page_num)

        # The roll is ROLL_WIDTH_MM wide. Pages must fit within that width.
        # If page width > roll width, scale proportionally.
        # The page "width" on the roll is the shorter dimension of the page
        # (pages are oriented so the shorter side goes across the roll).
        if max(w_mm, h_mm) <= ROLL_WIDTH_MM + 6.0:
            # Both dimensions fit the roll width. Orient the larger dimension across the roll to save paper length.
            page_across = max(w_mm, h_mm)
            page_along = min(w_mm, h_mm)
        else:
            # At least one dimension exceeds roll width. The smaller dimension MUST go across the roll.
            page_across = min(w_mm, h_mm)
            page_along = max(w_mm, h_mm)

        # Scale: the across-dimension maps to roll width (or less)
        if page_across > ROLL_WIDTH_MM:
            # Scale down proportionally
            scale_factor = ROLL_WIDTH_MM / page_across
            page_across *= scale_factor
            page_along *= scale_factor

        # Convert to display pixels
        disp_across = page_across * MM_TO_PX
        disp_along = page_along * MM_TO_PX

        # On the canvas: across = horizontal (width), along = vertical (height)
        disp_w = disp_across
        disp_h = disp_along

        actual_w_mm = w_mm
        actual_h_mm = h_mm

        # If the original page width does not match the chosen across-dimension,
        # it means we need to rotate it so it fits the roll as calculated.
        
        # Apply 99.94% scale as requested
        scale_9994 = 0.9994
        disp_w *= scale_9994
        disp_h *= scale_9994
        
        if abs(actual_w_mm - page_across) > 1.0:
            img = self._render_page(filepath, page_num, disp_h, disp_w)
            img = img.rotate(90, expand=True)
        else:
            img = self._render_page(filepath, page_num, disp_w, disp_h)

        self.cn_count += 1
        self._q.put(("cn", img, fmt, page_across, page_along))

    def _place_cn(self, photo, fmt, across_mm, along_mm):
        c = self.cn_canvas
        rx = 30  # roll X start
        margin = 6

        # Ensure the image doesn't exceed roll display width
        img_w = min(photo.width(), ROLL_DISPLAY_WIDTH)
        
        # Right-align on roll
        page_x = rx + ROLL_DISPLAY_WIDTH - img_w

        # Cut line
        c.create_line(rx, self.cn_y, rx + ROLL_DISPLAY_WIDTH, self.cn_y,
                      fill="#cc0000", dash=(6, 3), width=1)

        # Place image
        c.create_image(page_x, self.cn_y + 2, image=photo, anchor="nw")

        # Page border
        c.create_rectangle(page_x, self.cn_y + 2,
                           page_x + img_w, self.cn_y + 2 + photo.height(),
                           outline="#3366cc", dash=(4, 4), width=1)

        # Bottom cut line
        c.create_line(rx, self.cn_y + 2 + photo.height() + margin // 2, 
                      rx + ROLL_DISPLAY_WIDTH, self.cn_y + 2 + photo.height() + margin // 2,
                      fill="#cc0000", dash=(6, 3), width=1)

        # Format + dimensions label on right
        label = f"{fmt}\n{across_mm:.0f}×{along_mm:.0f}мм"
        c.create_text(rx + ROLL_DISPLAY_WIDTH + 12, self.cn_y + photo.height() // 2,
                      text=label, fill="#333", font=("Segoe UI", 8), anchor="w")

        self.cn_y += photo.height() + margin

        # Extend roll bg
        c.delete("roll_bg")
        c.create_rectangle(rx, 0, rx + ROLL_DISPLAY_WIDTH, self.cn_y + 200,
                           fill="#f5f5f0", outline="#bbb", width=1, tags="roll_bg")
        c.tag_lower("roll_bg")

        c.configure(scrollregion=(0, 0, rx + ROLL_DISPLAY_WIDTH + 120, max(self.cn_y + 100, 500)))

    # ────────────── Simulation ──────────────

    def _run_simulation(self):
        # Laser (HP)
        for fp, fn, pgs, inf in self.plan.get("laser", []):
            if inf["missing"] <= 0:
                continue
            for p in pgs:
                if self._stop.is_set(): return
                self._log(f"[HP] стр.{p} из {fn} ({inf['format']})")
                self._draw_hp_page(fp, p, inf["format"])
                time.sleep(0.1)

        # Plotter (Canon)
        for fp, fn, pgs, inf in self.plan.get("plotter", []):
            if inf["missing"] <= 0:
                continue
            for p in pgs:
                if self._stop.is_set(): return
                self._log(f"[Canon] стр.{p} из {fn} ({inf['format']})")
                self._draw_cn_page(fp, p, inf["format"])
                time.sleep(0.15)

        self._log(f"\n═══ Симуляция завершена ═══")
        self._log(f"HP: {self.hp_count} листов")
        self._log(f"Canon: {self.cn_count} листов")

    def _draw_all(self):
        for fp, fn, pgs, inf in self.plan.get("laser", []):
            if inf["missing"] <= 0:
                continue
            for p in pgs:
                if self._stop.is_set(): return
                self._draw_hp_page(fp, p, inf["format"])

        for fp, fn, pgs, inf in self.plan.get("plotter", []):
            if inf["missing"] <= 0:
                continue
            for p in pgs:
                if self._stop.is_set(): return
                self._draw_cn_page(fp, p, inf["format"])


def _make_scrollbar(parent, canvas):
    from tkinter import ttk
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    return scrollbar
