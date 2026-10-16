import threading
import queue
import time
import logging
import tempfile
import os
import gc
try:
    import fitz
except ImportError:
    import pymupdf as fitz

# Global lock to prevent multiple worker threads from doing heavy PDF extraction/spooling simultaneously.
# This prevents RAM spikes and system freezes when printing huge files to both Laser and Plotter at once.
HEAVY_PROCESSING_LOCK = threading.Lock()
_heavy_owner = threading.local()


def _acquire_heavy():
    HEAVY_PROCESSING_LOCK.acquire()
    _heavy_owner.held = True


def _release_heavy():
    """Освобождает замок, только если его держит ТЕКУЩИЙ поток.
    Раньше было `if LOCK.locked(): LOCK.release()` — поток лазера мог снять замок,
    который держал поток плоттера, и тяжёлая обработка шла параллельно (скачки RAM)."""
    if getattr(_heavy_owner, "held", False):
        _heavy_owner.held = False
        HEAVY_PROCESSING_LOCK.release()

class PrintJob:
    def __init__(self, filepath, printer_name, engine, settings, copies, pages=None, format_name="", scale=100.0, rasterize_dpi=300):
        self.filepath = filepath
        self.printer_name = printer_name
        self.engine = engine
        self.settings = settings
        self.copies = copies
        self.pages = pages
        self.format_name = format_name
        self.scale = scale
        self.rasterize_dpi = rasterize_dpi

class PauseJob:
    def __init__(self, message, title="Внимание"):
        self.message = message
        self.title = title
        self.event = threading.Event()
        self.cancelled = False

class PrintQueueManager:
    def __init__(self):
        self.queues = {}
        self.threads = {}
        self._stop_event = threading.Event()
        self.on_job_start = None
        self.on_job_complete = None
        self.on_job_error = None
        
    def start(self):
        self._stop_event.clear()
        for p in self.queues.keys():
            self._ensure_thread(p)

    def stop(self):
        self._stop_event.set()

    def _ensure_thread(self, printer_name):
        if printer_name not in self.queues:
            self.queues[printer_name] = queue.Queue()
        if printer_name not in self.threads or not self.threads[printer_name].is_alive():
            t = threading.Thread(target=self._worker_loop, args=(printer_name,), daemon=True)
            self.threads[printer_name] = t
            t.start()

    def add_job(self, filepath, printer_name, engine, settings, copies=1, pages=None, format_name="", scale=100.0, rasterize_dpi=300):
        job = PrintJob(filepath, printer_name, engine, settings, copies, pages, format_name, scale, rasterize_dpi)
        self._ensure_thread(printer_name)
        self.queues[printer_name].put(job)
        
    def add_pause(self, printer_name, message, title="Внимание"):
        job = PauseJob(message, title)
        self._ensure_thread(printer_name)
        self.queues[printer_name].put(job)
        return job
        
    def clear(self):
        for q in self.queues.values():
            self._drain(q)

    @staticmethod
    def _drain(q):
        """Убрать все ожидающие задания, корректно уменьшив счётчик незавершённых
        (иначе pending_count() навсегда считал бы очередь занятой)."""
        with q.mutex:
            n = len(q.queue)
            q.queue.clear()
            q.unfinished_tasks = max(0, q.unfinished_tasks - n)
            if q.unfinished_tasks == 0:
                q.all_tasks_done.notify_all()

    def pending_count(self):
        """Сколько заданий ещё в очереди или печатается прямо сейчас (по всем принтерам)."""
        return sum(q.unfinished_tasks for q in list(self.queues.values()))

    def is_busy(self):
        return self.pending_count() > 0

    def _worker_loop(self, printer_name):
        q = self.queues[printer_name]
        while not self._stop_event.is_set():
            try:
                job = q.get(timeout=1.0)
            except queue.Empty:
                continue

            if self._stop_event.is_set():
                break

            if isinstance(job, PauseJob):
                if self._stop_event.is_set():
                    q.task_done()
                    continue
                    
                # Trigger UI to show messagebox
                if hasattr(self, 'on_pause_request') and self.on_pause_request:
                    self.on_pause_request(job)
                else:
                    job.event.set()
                    
                # Wait for user to click OK
                job.event.wait()
                
                if job.cancelled:
                    self._drain(q)
                    logging.info(f"Queue for {printer_name} cancelled by user at pause prompt.")
                    
                q.task_done()
                continue

            if self.on_job_start:
                self.on_job_start(job)
            
            temp_file = None
            try:
                is_plotter = "plotter" in printer_name.lower() or "canon" in printer_name.lower()
                target_file = job.filepath
                final_settings = job.settings or ""
                
                # Determine if this is a plotter job
                from core.pdf_utils import is_laser
                is_plotter = not is_laser(job.format_name)
                
                _acquire_heavy()
                
                # Slice PDF by physical pages if specific pages are requested
                if job.pages:
                    try:
                        doc = fitz.open(job.filepath)
                        new_doc = fitz.open()
                        
                        logging.info(f"[Plotter] Processing {os.path.basename(job.filepath)}, "
                                     f"format={job.format_name}, is_plotter={is_plotter}, "
                                     f"pages={job.pages}, copies={job.copies}")
                        
                        canvas_w_pts = None
                        canvas_h_pts = None
                        
                        fmt_lower = job.format_name.lower() if job.format_name else ""
                        is_a4xN = is_plotter and "a4x" in fmt_lower
                        
                        # We flatten copies into the PDF document itself to avoid Foxit race conditions!
                        print_loop_copies = 1
                        
                        unrolled_pages = []
                        for _ in range(job.copies):
                            unrolled_pages.extend(job.pages)
                        
                        if is_a4xN:
                            logging.info(f"[Plotter] Detected A4xN format ({job.format_name}). Packing pages side-by-side (flattening {job.copies} copies).")
                            
                            for i in range(0, len(unrolled_pages), 2):
                                p1_num = unrolled_pages[i]
                                p2_num = unrolled_pages[i+1] if i+1 < len(unrolled_pages) else None
                                
                                p1 = doc.load_page(p1_num - 1)
                                p1.set_rotation(0) # Normalize to avoid rotation-induced plotter gaps
                                rot1 = 0
                                w1, h1 = p1.rect.width, p1.rect.height
                                if w1 > h1:
                                    rot1 = -90
                                    w1, h1 = h1, w1
                                    
                                if p2_num is not None:
                                    p2 = doc.load_page(p2_num - 1)
                                    p2.set_rotation(0) # Normalize to avoid rotation-induced plotter gaps
                                    rot2 = 0
                                    w2, h2 = p2.rect.width, p2.rect.height
                                    if w2 > h2:
                                        rot2 = -90
                                        w2, h2 = h2, w2
                                        
                                    new_w = w1 + w2
                                    new_h = max(h1, h2)
                                    new_page = new_doc.new_page(width=new_w, height=new_h)
                                    new_page.show_pdf_page(fitz.Rect(0, 0, w1, h1), doc, p1.number, rotate=rot1, keep_proportion=False)
                                    new_page.show_pdf_page(fitz.Rect(w1, 0, new_w, h2), doc, p2.number, rotate=rot2, keep_proportion=False)
                                    logging.info(f"[Plotter] Packed {p1_num} and {p2_num} side-by-side ({new_w:.0f}x{new_h:.0f}pts = {new_w*25.4/72:.0f}x{new_h*25.4/72:.0f}mm).")
                                else:
                                    new_page = new_doc.new_page(width=w1, height=h1)
                                    new_page.show_pdf_page(fitz.Rect(0, 0, w1, h1), doc, p1.number, rotate=rot1, keep_proportion=False)
                                    logging.info(f"[Plotter] Packed {p1_num} alone (odd number of pages).")
                                    
                                # Rotate the packed page 180 degrees (User request)
                                new_page.set_rotation(new_page.rotation + 180)
                                
                            # All packed pages have the same fixed dimensions
                            canvas_w_pts = max([p.rect.width for p in new_doc])
                            canvas_h_pts = max([p.rect.height for p in new_doc])
                            logging.info(f"[Plotter] A4xN canvas: {canvas_w_pts:.0f}x{canvas_h_pts:.0f}pts = {canvas_w_pts*25.4/72:.1f}x{canvas_h_pts*25.4/72:.1f}mm")
                        else:
                            # Highly efficient extraction for non-A4xN formats
                            is_extended_format = "x" in fmt_lower and not fmt_lower.startswith("unk")
                            
                            if is_plotter and not is_extended_format:
                                # Plotter DEVMODE HACK formatting: use insert_pdf to bake rotation metadata into the page MediaBox 
                                # so that Foxit's DevMode hack sees true physical dimensions
                                new_doc = fitz.open()
                                for p_idx, original_p in enumerate(unrolled_pages):
                                    page = doc[original_p - 1]
                                    
                                    # 1. Ensure width fits the 610mm roll (allow up to 614mm)
                                    rect = page.rect
                                    w_mm_page = rect.width * 25.4 / 72.0
                                    h_mm_page = rect.height * 25.4 / 72.0
                                    max_dim = max(w_mm_page, h_mm_page)
                                    is_portrait = w_mm_page < h_mm_page
                                    
                                    if max_dim <= 614: 
                                        if is_portrait:
                                            logging.info(f"[Plotter] Page {original_p} fits Landscape. Rotating -90 degrees to save paper.")
                                            page.set_rotation(page.rotation - 90)
                                    else:
                                        if w_mm_page > 614 and h_mm_page <= 614:
                                            logging.info(f"[Plotter] Page {original_p} is too wide ({w_mm_page:.1f}mm). Rotating 90 degrees to fit roll.")
                                            page.set_rotation(page.rotation + 90)
                                        elif w_mm_page > 614 and h_mm_page > 614:
                                            if w_mm_page > h_mm_page:
                                                logging.info(f"[Plotter] Page {original_p} too large for roll. Rotating 90 degrees to minimize clipping.")
                                                page.set_rotation(page.rotation + 90)
                                            
                                    # 2. Apply 180-degree rotation (plotter physical orientation)
                                    logging.info(f"[Plotter] Page {original_p} applying 180-degree rotation.")
                                    page.set_rotation(page.rotation + 180)
                                    
                                    # Insert into new doc (baking the rotation)
                                    new_doc.insert_pdf(doc, from_page=original_p-1, to_page=original_p-1)
                                    
                                # Use the last page dimensions for final_settings
                                canvas_w_pts = new_doc[-1].rect.width
                                canvas_h_pts = new_doc[-1].rect.height
                            else:
                                # Laser and Extended Plotter (A4x3, A2x3, etc.): 
                                # We must use insert_pdf instead of select() to ensure Foxit gets unique page dictionaries 
                                # (XREFs) for copies, otherwise Foxit prints blank pages for duplicates!
                                temp_source = fitz.open(job.filepath)
                                new_doc = fitz.open()
                                
                                for original_p in unrolled_pages:
                                    new_doc.insert_pdf(temp_source, from_page=original_p-1, to_page=original_p-1)
                                    
                                temp_source.close()
                                
                                # For extended formats, we still want to apply the 180-degree rotation tag
                                if is_plotter:
                                    for p_idx in range(len(new_doc)):
                                        page = new_doc[p_idx]
                                        
                                        # 1. Ensure width fits the 610mm roll
                                        rect = page.rect
                                        w_mm_page = rect.width * 25.4 / 72.0
                                        h_mm_page = rect.height * 25.4 / 72.0
                                        max_dim = max(w_mm_page, h_mm_page)
                                        is_portrait = w_mm_page < h_mm_page
                                        
                                        if max_dim <= 614:
                                            if is_portrait:
                                                logging.info(f"[Plotter] Extended Page {p_idx+1} fits Landscape. Rotating -90 degrees.")
                                                page.set_rotation(page.rotation - 90)
                                        else:
                                            if w_mm_page > 614 and h_mm_page <= 614:
                                                logging.info(f"[Plotter] Extended Page {p_idx+1} is too wide ({w_mm_page:.1f}mm). Rotating 90 degrees to fit roll.")
                                                page.set_rotation(page.rotation + 90)
                                            elif w_mm_page > 614 and h_mm_page > 614:
                                                if w_mm_page > h_mm_page:
                                                    logging.info(f"[Plotter] Extended Page {p_idx+1} too large. Rotating 90 degrees to minimize clipping.")
                                                    page.set_rotation(page.rotation + 90)
                                                
                                        # 2. Apply 180-degree rotation
                                        page.set_rotation(page.rotation + 180)
                                
                                canvas_w_pts = new_doc[-1].rect.width
                                canvas_h_pts = new_doc[-1].rect.height
                                
                                w_mm = canvas_w_pts * 25.4 / 72.0
                                h_mm = canvas_h_pts * 25.4 / 72.0
                                
                                logging.info(f"[Laser] Processed {len(unrolled_pages)} pages via insert_pdf(). Final size of last page: {w_mm:.1f}x{h_mm:.1f}mm "
                                             f"({canvas_w_pts:.1f}x{canvas_h_pts:.1f}pts), rotation={new_doc[-1].rotation}")
                                
                        # Force 100% scale for NON-A4xN Plotter formats to ensure perfect match with Foxit DevMode hack
                        # A4xN formats need scale applied because show_pdf_page already handles the content fitting.
                        if is_plotter and not is_a4xN:
                            job.scale = 100.0
                        
                        # Apply safe_scale_pdf if requested
                        if abs(job.scale - 100.0) > 0.001:
                            scale_factor = job.scale / 100.0
                            logging.info(f"Applying scale {job.scale}% to spool file")
                            for p_idx in range(len(new_doc)):
                                page = new_doc[p_idx]
                                
                                # Temporarily remove rotation to safely adjust boxes
                                saved_rot = page.rotation
                                page.set_rotation(0)
                                
                                native_mb = page.mediabox
                                nw_mb = native_mb.width / scale_factor
                                nh_mb = native_mb.height / scale_factor
                                page.set_mediabox(fitz.Rect(0, 0, nw_mb, nh_mb))
                                
                                # CropBox must match MediaBox exactly to avoid float precision errors
                                page.set_cropbox(page.mediabox)
                                
                                # Restore rotation
                                page.set_rotation(saved_rot)

                        # Save the processed PDF
                        tdir = tempfile.mkdtemp(prefix="pm_spool_")
                        fmt = job.format_name if job.format_name else "UNK"
                        base = os.path.basename(job.filepath)
                        name, _ = os.path.splitext(base)
                        name = name[:150]
                        temp_file = os.path.join(tdir, f"{fmt}_{name}.pdf")
                        
                        # Add a white background to LASER pages only to prevent Foxit from rendering transparent backgrounds as black.
                        # SKIP for plotter: draw_rect creates a massive white bounding box that causes the plotter to roll blank paper!
                        if not is_plotter:
                            for p_idx in range(len(new_doc)):
                                page = new_doc[p_idx]
                                page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=False)
                            
                        # Use garbage=3 and deflate=True to deeply optimize massive PDFs, fixing the 10GB spooler issue!
                        new_doc.save(temp_file, garbage=3, deflate=True)
                        new_doc.close()
                        doc.close()
                        gc.collect()
                        target_file = temp_file
                        
                        # ── Configure Plotter Settings ──
                        if is_plotter and canvas_w_pts is not None:
                            w_mm_int = int(round(canvas_w_pts * 25.4 / 72.0))
                            h_mm_int = int(round(canvas_h_pts * 25.4 / 72.0))
                            
                            # NOTE: Do NOT add margin here. The +2mm was causing visible white "frame" borders
                            # around the printed content on the plotter. The PDF content already fits exactly.
                            
                            # Simple DevMode hack: Just set the paper size to W+2 x L+2, Portrait.
                            # We already natively rotated the PDF in PyMuPDF!
                            # Foxit will see WxL PDF fitting into (W+2)x(L+2) Paper → prints 1:1 without auto-rotating.
                            orientation = 1
                            final_settings = f"DEVMODE_HACK_{w_mm_int}_{h_mm_int}_{orientation}"
                            logging.info(f"[Plotter] Settings set to: {final_settings}")
                        elif not is_plotter:
                            fmt_lower = job.format_name.lower() if job.format_name else ""
                            # If A4 or A3, force DevMode hack to select correct tray because Foxit CLI doesn't support paper size argument
                            if fmt_lower in ["a4", "а4"]:
                                orientation = 1
                                final_settings = f"DEVMODE_HACK_210_297_{orientation}_9"
                                logging.info(f"[Laser] Detected A4. Flagging for Foxit DevMode Hack to enforce A4 tray.")
                            elif fmt_lower in ["a3", "а3"]:
                                orientation = 1
                                final_settings = f"DEVMODE_HACK_297_420_{orientation}_8"
                                logging.info(f"[Laser] Detected A3. Flagging for Foxit DevMode Hack to enforce A3 tray.")
                            else:
                                final_settings = settings if settings else ""
                        
                    except Exception as e:
                        logging.error(f"Formatting failed for {job.filepath}: {e}", exc_info=True)
                        try:
                            doc = fitz.open(job.filepath)
                            new_doc = fitz.open()
                            for p in job.pages:
                                new_doc.insert_pdf(doc, from_page=p-1, to_page=p-1)
                            tdir = tempfile.mkdtemp(prefix="pm_spool_")
                            temp_file = os.path.join(tdir, f"raw_{os.path.basename(job.filepath)}")
                            new_doc.save(temp_file)
                            new_doc.close()
                            doc.close()
                            gc.collect()
                            target_file = temp_file
                        except Exception as e2:
                            logging.error(f"Raw extraction failed for {job.filepath}: {e2}")
                            if self.on_job_error:
                                self.on_job_error(job, f"Не удалось извлечь страницы: {e2}")
                            _release_heavy()
                            continue  # task_done() вызовется в finally (раньше вызывался дважды)

                try:
                    if final_settings.startswith("DEVMODE_HACK_"):
                        # Bypass standard logic and use Foxit Double-Negative DevMode hack
                        parts = final_settings.split("_")
                        w_mm = int(parts[2])
                        h_mm = int(parts[3])
                        orientation = int(parts[4]) if len(parts) > 4 else 2
                        paper_size = int(parts[5]) if len(parts) > 5 else 256
                        
                        # Pre-rasterize heavy formats to speed up Foxit spooling
                        if self._should_rasterize(job.format_name):
                            raster_file = self._rasterize_pdf(target_file, dpi=job.rasterize_dpi)
                            if raster_file:
                                # Use rasterized file instead of original
                                if target_file != job.filepath and os.path.exists(target_file):
                                    try: os.remove(target_file)
                                    except: pass
                                target_file = raster_file
                        
                        logging.info(f"[Print] Using Foxit DevMode Hack for {w_mm}x{h_mm} (Orientation {orientation}, PaperSize {paper_size}) to '{job.printer_name}'...")
                        success = self._print_devmode_hack(target_file, job.printer_name, w_mm, h_mm, orientation, print_loop_copies, paper_size)
                    elif final_settings.startswith("SUMATRA_HACK_"):
                        parts = final_settings.split("_")
                        w_mm = int(parts[2])
                        h_mm = int(parts[3])
                        logging.info(f"[Print] Rasterizing {w_mm}x{h_mm} format at 300 DPI for SumatraPDF...")
                        
                        try:
                            t0 = time.time()
                            doc = fitz.open(target_file)
                            raster_doc = fitz.open()
                            for page in doc:
                                pix = page.get_pixmap(dpi=300)
                                new_page = raster_doc.new_page(width=pix.width, height=pix.height)
                                new_page.insert_image(new_page.rect, pixmap=pix)
                            
                            raster_file = os.path.join(os.path.dirname(target_file), f"raster_{os.path.basename(target_file)}")
                            raster_doc.save(raster_file, deflate=True)
                            raster_doc.close()
                            doc.close()
                            
                            logging.info(f"[Print] Rasterization complete in {time.time()-t0:.2f}s. Using SumatraEngine.")
                            from printing.sumatra_engine import SumatraEngine
                            from core.config import get_base_path
                            sumatra = SumatraEngine(os.path.join(get_base_path(), "tools", "SumatraPDF.exe"))
                            sumatra_settings = f"paper={w_mm}x{h_mm}mm,shrink"
                            success = sumatra.print_file(raster_file, job.printer_name, sumatra_settings, print_loop_copies)
                            
                            if os.path.exists(raster_file):
                                os.remove(raster_file)
                        except Exception as e:
                            logging.error(f"[Print] Rasterized printing failed: {e}")
                            success = False
                    else:
                        logging.info(f"[Print] Sending to '{job.printer_name}': {target_file} "
                                     f"(settings='{final_settings}', copies={print_loop_copies})")
                                     
                        engine_to_use = job.engine
                        
                        # SumatraEngine natively handles copies and prevents blank pages
                        # Foxit Engine fails on multiple copies because it exits early and deletes the temp file.
                        # So we stick to the chosen engine (which handles copies properly).
                            
                        success = engine_to_use.print_file(
                            target_file, 
                            job.printer_name, 
                            final_settings, 
                            print_loop_copies
                        )
                    logging.info(f"[Print] Engine returned: success={success}")
                    
                    _release_heavy()
                    
                    if success:
                        actual_pages_printed = self._monitor_spooler(job.printer_name, os.path.basename(target_file), job)
                        # If we packed pages, the spooler will report fewer pages printed. 
                        # We just assume success = all original pages printed.
                        if self.on_job_complete:
                            self.on_job_complete(job, None) # None forces 100% completion in app.py
                    else:
                        if self.on_job_error:
                            self.on_job_error(job, "Engine returned False.")
                        
                except Exception as e:
                    _release_heavy()
                    logging.error(f"Engine exception during printing: {e}", exc_info=True)
                    if self.on_job_error:
                        self.on_job_error(job, str(e))
                
                # Cleanup temp file
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                        t_dir = os.path.dirname(temp_file)
                        if os.path.basename(t_dir).startswith("pm_spool_"):
                            os.rmdir(t_dir)
                    except Exception as e:
                        logging.error(f"Failed to remove temp file {temp_file}: {e}")
                
                # Short sleep to prevent spooler from hanging
                time.sleep(2)
            
            except Exception as e:
                logging.error(f"Error processing job: {e}")
                if self.on_job_error:
                    self.on_job_error(job, str(e))
                _release_heavy()
            finally:
                _release_heavy()  # гарантированно, даже при неожиданной ошибке
                q.task_done()

    def _should_rasterize(self, format_name):
        """Returns True if the format is heavy enough to benefit from pre-rasterization (weight >= 12)."""
        import re
        f = str(format_name).upper().replace(" ", "")
        if "A4" in f and "X" not in f: return False  # weight 1
        if "A3" in f and "X" not in f: return False  # weight 2
        if "A2" in f and "X" not in f: return False  # weight 4
        if "A1" in f and "X" not in f: return False  # weight 8
        if "A0" in f and "X" not in f: return True   # weight 16
        m = re.search(r'A(\d)X(\d+)', f)
        if m:
            base = int(m.group(1))
            mult = int(m.group(2))
            base_w = {4: 1, 3: 2, 2: 4, 1: 8, 0: 16}.get(base, 1)
            return base_w * mult >= 12
        return False

    def _rasterize_pdf(self, filepath, dpi=300):
        """Rasterize a PDF to a pixel-based PDF at the given DPI. Returns path to new file, or None on failure."""
        try:
            t0 = time.time()
            doc = fitz.open(filepath)
            raster_doc = fitz.open()
            
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                # alpha=False ensures white background (no black squares from transparency)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                new_page = raster_doc.new_page(width=pix.width * 72 / dpi, height=pix.height * 72 / dpi)
                new_page.insert_image(new_page.rect, pixmap=pix)
                logging.info(f"[Rasterize] Page {page_idx+1}: {pix.width}x{pix.height}px at {dpi} DPI")
            
            raster_file = os.path.join(os.path.dirname(filepath), f"raster_{os.path.basename(filepath)}")
            raster_doc.save(raster_file, deflate=True)
            raster_doc.close()
            doc.close()
            gc.collect()
            
            elapsed = time.time() - t0
            logging.info(f"[Rasterize] Completed {dpi} DPI rasterization in {elapsed:.1f}s → {raster_file}")
            return raster_file
            
        except Exception as e:
            logging.error(f"[Rasterize] Failed: {e}", exc_info=True)
            return None

    def _monitor_spooler(self, printer_name, doc_basename, job):
        try:
            import win32print
        except ImportError:
            logging.warning("win32print not found. Skipping spooler monitoring.")
            return
            
        import time
        import logging
        
        try:
            hprinter = win32print.OpenPrinter(printer_name)
        except Exception as e:
            logging.error(f"Failed to open printer {printer_name} for monitoring: {e}")
            return
            
        try:
            # Spooler might take a moment to receive the job
            target_job_id = None
            for _ in range(10): # 10 seconds timeout to find job
                jobs = win32print.EnumJobs(hprinter, 0, -1, 2)
                if jobs:
                    for j in jobs:
                        if doc_basename in j['pDocument']:
                            target_job_id = j['JobId']
                            break
                    if not target_job_id:
                        target_job_id = max(j['JobId'] for j in jobs)
                        
                if target_job_id:
                    break
                time.sleep(1)
                
            if not target_job_id:
                logging.warning(f"Could not find job for {doc_basename} in spooler for {printer_name}.")
                return
                
            logging.info(f"Found job {target_job_id} for {doc_basename}. Monitoring...")
            
            last_alert_time = 0
            last_total = 1
            last_size = 0
            last_pages = 0
            
            # Start monitoring loop
            while not self._stop_event.is_set():
                # Check printer status
                try:
                    info = win32print.GetPrinter(hprinter, 2)
                    p_status = info['Status']
                    
                    error_msg = None
                    attributes = info.get('Attributes', 0)
                    if p_status & win32print.PRINTER_STATUS_PAPER_OUT:
                        error_msg = "Закончилась бумага / рулон"
                    elif p_status & win32print.PRINTER_STATUS_NO_TONER:
                        error_msg = "Закончился картридж / тонер"
                    elif p_status & win32print.PRINTER_STATUS_DOOR_OPEN:
                        error_msg = "Открыта дверца принтера"
                    elif p_status & win32print.PRINTER_STATUS_PAPER_JAM:
                        error_msg = "Замятие бумаги"
                    elif p_status & win32print.PRINTER_STATUS_OFFLINE or attributes & win32print.PRINTER_ATTRIBUTE_WORK_OFFLINE:
                        error_msg = "Принтер отключен (Offline)"
                    elif p_status & win32print.PRINTER_STATUS_ERROR:
                        error_msg = "Возможная ошибка принтера"
                        
                    if error_msg:
                        current_time = time.time()
                        if current_time - last_alert_time > 10:
                            if hasattr(self, 'on_printer_alert') and self.on_printer_alert:
                                self.on_printer_alert(printer_name, error_msg)
                            last_alert_time = current_time
                        time.sleep(2) # Wait before checking again
                        continue
                        
                except Exception as e:
                    logging.error(f"Failed to check printer status: {e}")
                    
                # Check job status
                try:
                    jobs = win32print.EnumJobs(hprinter, 0, -1, 2)
                    job_found = False
                    for j in jobs:
                        if j['JobId'] == target_job_id:
                            job_found = True
                            pages = j.get('PagesPrinted', 0)
                            total = j.get('TotalPages', 0)
                            size = j.get('Size', 0)
                            if total > 0: last_total = total
                            if size > 0: last_size = size
                            if pages > 0: last_pages = pages
                            
                            # If spooler says "Printing", we can assume at least 1 page is being processed
                            # if it hasn't updated PagesPrinted yet.
                            if j.get('Status', 0) & win32print.JOB_STATUS_PRINTING and pages == 0 and total > 0:
                                pages = 1
                            
                            if hasattr(self, 'on_job_progress') and self.on_job_progress:
                                self.on_job_progress(job, pages, total, size)
                                
                            if j.get('Status', 0) & win32print.JOB_STATUS_PRINTED:
                                logging.info(f"Job {target_job_id} marked as PRINTED.")
                                if hasattr(self, 'on_job_progress') and self.on_job_progress:
                                    self.on_job_progress(job, last_total, last_total, last_size)
                                return last_total
                            break
                            
                    if not job_found:
                        logging.info(f"Job {target_job_id} disappeared from spooler.")
                        if hasattr(self, 'on_job_progress') and self.on_job_progress:
                            self.on_job_progress(job, last_total, last_total, last_size)
                        return last_total
                        
                except Exception as e:
                    logging.error(f"Failed to check job status: {e}")
                    return last_pages
                    
                time.sleep(1)
                
        finally:
            win32print.ClosePrinter(hprinter)
        
        return last_pages

    def _print_devmode_hack(self, filepath, printer_name, w_mm, h_mm, orientation=2, copies=1, paper_size=256):
        import win32print
        import win32con
        import subprocess
        
        # 1. Find Foxit
        foxit_path = None
        paths = [
            r"C:\Program Files (x86)\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
            r"C:\Program Files\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
            r"C:\Program Files (x86)\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
            r"C:\Program Files\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
            r"C:\Program Files (x86)\Foxit Software\Foxit Reader\FoxitReader.exe",
            r"C:\Program Files\Foxit Software\Foxit Reader\FoxitReader.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                foxit_path = p
                break
                
        if not foxit_path:
            logging.error("Foxit not found for DevMode hack!")
            return False
        
        hprinter = None
        info_orig = None
        set_printer_ok = False
        
        try:
            # Try to open with PRINTER_ALL_ACCESS first (needs admin)
            try:
                hprinter = win32print.OpenPrinter(printer_name, {"DesiredAccess": win32print.PRINTER_ALL_ACCESS})
            except Exception:
                # Fall back to normal access
                hprinter = win32print.OpenPrinter(printer_name)
            
            # Save original settings
            info_orig = win32print.GetPrinter(hprinter, 9)
            devmode_orig = info_orig['pDevMode']
            if devmode_orig is None:
                devmode_orig = win32print.GetPrinter(hprinter, 2)['pDevMode']
                info_orig['pDevMode'] = devmode_orig
                
            # Build new DevMode
            info_new = win32print.GetPrinter(hprinter, 9)
            if info_new['pDevMode'] is None:
                info_new['pDevMode'] = win32print.GetPrinter(hprinter, 2)['pDevMode']
                
            devmode_new = info_new['pDevMode']
            devmode_new.PaperSize = paper_size
            devmode_new.PaperWidth = int(w_mm * 10)  # tenths of mm
            devmode_new.PaperLength = int(h_mm * 10)
            devmode_new.Orientation = orientation
            # DM_PAPERSIZE | DM_PAPERLENGTH | DM_ORIENTATION | DM_PAPERWIDTH
            devmode_new.Fields |= (0x0008 | 0x0004 | 0x0001 | 0x0002)
            
            # Try SetPrinter Level 9 (globally changes printer defaults — needs admin)
            try:
                win32print.SetPrinter(hprinter, 9, info_new, 0)
                set_printer_ok = True
                logging.info(f"[DevMode] SetPrinter Level 9 succeeded for {printer_name}: "
                             f"Paper={paper_size}, {w_mm}x{h_mm}mm, Orient={orientation}")
            except Exception as e:
                logging.warning(f"[DevMode] SetPrinter Level 9 failed (need admin?): {e}. "
                                f"Falling back to DocumentProperties approach.")
                # Fallback: Use DocumentProperties to set DevMode for THIS print job only.
                # This modifies the DevMode in-memory and Foxit will pick it up from the default printer settings.
                try:
                    import win32gui
                    # DocumentProperties with DM_IN_BUFFER | DM_OUT_BUFFER applies devmode without needing admin
                    DM_IN_BUFFER = 8
                    DM_OUT_BUFFER = 2
                    result = win32print.DocumentProperties(
                        win32gui.GetDesktopWindow(),  # hwnd
                        hprinter,
                        printer_name,
                        devmode_new,   # output
                        devmode_new,   # input
                        DM_IN_BUFFER | DM_OUT_BUFFER
                    )
                    if result == 1:  # IDOK
                        # Now try SetPrinter with the validated devmode
                        info_new['pDevMode'] = devmode_new
                        try:
                            win32print.SetPrinter(hprinter, 9, info_new, 0)
                            set_printer_ok = True
                            logging.info(f"[DevMode] DocumentProperties + SetPrinter succeeded for {printer_name}")
                        except Exception as e2:
                            logging.warning(f"[DevMode] SetPrinter still failed after DocumentProperties: {e2}. "
                                            f"Printing with default settings.")
                    else:
                        logging.warning(f"[DevMode] DocumentProperties returned {result}. Printing with defaults.")
                except Exception as e3:
                    logging.warning(f"[DevMode] DocumentProperties fallback failed: {e3}. Printing with defaults.")
            
            # 2. Print with Foxit
            cmd = [foxit_path, "/t", filepath, printer_name]
            logging.info(f"Running Foxit DevMode Hack: {cmd} (copies={copies})")
            for i in range(copies):
                subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                if i < copies - 1:
                    time.sleep(1)
            
            # 3. Restore original settings (only if we changed them)
            time.sleep(5)
            if set_printer_ok:
                try:
                    win32print.SetPrinter(hprinter, 9, info_orig, 0)
                    logging.info(f"[DevMode] Restored original printer settings for {printer_name}")
                except Exception as e:
                    logging.warning(f"[DevMode] Failed to restore printer settings: {e}")
            
            win32print.ClosePrinter(hprinter)
            return True
            
        except Exception as e:
            logging.error(f"DevMode hack failed: {e}", exc_info=True)
            try:
                if set_printer_ok and info_orig:
                    win32print.SetPrinter(hprinter, 9, info_orig, 0)
                if hprinter:
                    win32print.ClosePrinter(hprinter)
            except:
                pass
            return False
