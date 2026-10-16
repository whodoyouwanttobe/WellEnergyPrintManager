import customtkinter as ctk

def apply_theme(mode="dark"):
    ctk.set_appearance_mode(mode)
    ctk.set_default_color_theme("blue")

class Colors:
    SUCCESS = "#2ecc71"
    WARNING = "#f1c40f"
    ERROR = "#e74c3c"
    INFO = "#3498db"
    TEXT_MAIN = ("#000000", "#FFFFFF")
    TEXT_MUTED = ("#666666", "#AAAAAA")
    BG_CARD = ("#F0F0F0", "#2B2B2B")
    
class Fonts:
    def __init__(self):
        self.main = ctk.CTkFont(family="Segoe UI", size=13)
        self.main_bold = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        self.title = ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
        self.mono = ctk.CTkFont(family="Consolas", size=12)
