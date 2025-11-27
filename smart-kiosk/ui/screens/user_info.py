# ui/screens/user_info.py
import tkinter as tk

class UserInfoFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#253f58")
        self.controller = controller

        self.left = tk.Frame(self, bg="#253f58")
        self.center = tk.Frame(self, bg="#253f58")
        self.right = tk.Frame(self, bg="#253f58")

        self.left.pack(side="left", padx=20, pady=10)
        self.center.pack(side="left", expand=True)
        self.right.pack(side="right", padx=20, pady=10)

        self.usertype_label = tk.Label(self.left, text="USER", font=("Arial", 14, "bold"),
                                       bg="#3498db", fg="white", padx=20, pady=5)
        self.usertype_label.pack(anchor="w")

        self.name_label = tk.Label(self.left, text="Name: -", font=("Arial", 12),
                                   bg="#253f58", fg="white")
        self.name_label.pack(anchor="w", pady=(5, 0))

        self.id_label = tk.Label(self.center, text="ID: -", font=("Arial", 14), bg="#253f58", fg="white")
        self.charge_label = tk.Label(self.center, text="Charge: 0 sec", font=("Arial", 14), bg="#253f58", fg="white")
        self.water_label = tk.Label(self.center, text="Water: 0 ml", font=("Arial", 14), bg="#253f58", fg="white")

        self.id_label.pack(anchor="center")
        self.charge_label.pack(anchor="center")
        self.water_label.pack(anchor="center")

    def refresh(self):
        uid = self.controller.active_uid
        if not uid:
            self._clear()
            return

        user = {}
        try:
            user = self.controller.read_user(uid) or {}
        except Exception:
            user = {}

        user_type = user.get("type", "nonmember")
        if user_type == "nonmember" or user_type == "guest":
            badge = "GUEST"
            name = user.get("name", "Guest User")
        elif user_type == "member":
            badge = "MEMBER"
            name = user.get("name", "Member")
        elif user_type == "subscriber":
            badge = "SUBSCRIBER"
            name = user.get("name", "Subscriber")
        else:
            badge = user_type.upper()
            name = user.get("name", "-")

        self.usertype_label.config(text=badge)
        self.name_label.config(text=f"Name: {name}")
        self.id_label.config(text=f"ID: {uid}")

        charge = user.get("charge_balance", 0) or 0
        water = user.get("water_balance", 0) or user.get("temp_water_time", 0) or 0

        self.charge_label.config(text=f"Charge: {charge} sec")
        self.water_label.config(text=f"Water: {water} ml")

    def _clear(self):
        self.usertype_label.config(text="USER")
        self.name_label.config(text="Name: -")
        self.id_label.config(text="ID: -")
        self.charge_label.config(text="Charge: 0 sec")
        self.water_label.config(text="Water: 0 ml")
