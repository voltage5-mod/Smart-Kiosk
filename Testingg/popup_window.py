import tkinter as tk
from tkinter import ttk

class WaterCoinPopup:
    def __init__(self, parent):
        self.total_coins = 0
        self.total_credit = 0

        # Create popup window
        self.popup = tk.Toplevel(parent)
        self.popup.title("💧 Water Service - Coin Summary")
        self.popup.geometry("320x180")
        self.popup.resizable(False, False)
        self.popup.configure(bg="#1e1e1e")

        ttk.Label(self.popup, text="WATER SERVICE CREDIT", font=("Segoe UI", 14, "bold")).pack(pady=10)

        self.coin_label = ttk.Label(self.popup, text="Total Coins: ₱0", font=("Segoe UI", 12))
        self.coin_label.pack(pady=5)

        self.credit_label = ttk.Label(self.popup, text="Total Credit: 0 mL", font=("Segoe UI", 12))
        self.credit_label.pack(pady=5)

        ttk.Button(self.popup, text="Close", command=self.popup.destroy).pack(pady=10)

    def update(self, coin_value, credit_value):
        """Add new coin and update display"""
        self.total_coins += coin_value
        self.total_credit += credit_value
        self.coin_label.config(text=f"Total Coins: ₱{self.total_coins}")
        self.credit_label.config(text=f"Total Credit: {self.total_credit} mL")

    def reset(self):
        self.total_coins = 0
        self.total_credit = 0
        self.coin_label.config(text="Total Coins: ₱0")
        self.credit_label.config(text="Total Credit: 0 mL")
