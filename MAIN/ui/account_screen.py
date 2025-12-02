import tkinter as tk
from tkinter import messagebox
import firebase_helpers

class AccountScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        self.firebase = firebase_helpers
        
        self.setup_ui()
    
    def setup_ui(self):
        # Header
        tk.Label(self, text="WATER & CHARGING VENDO", font=("Arial", 32, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(40, 10))
        
        tk.Label(self, text="Scan RFID Card or Enter UID", font=("Arial", 18),
                 fg="#ecf0f1", bg="#2c3e50").pack(pady=(0, 20))
        
        # RFID Entry
        entry_frame = tk.Frame(self, bg="#2c3e50")
        entry_frame.pack(pady=20)
        
        tk.Label(entry_frame, text="RFID UID:", font=("Arial", 16),
                 fg="white", bg="#2c3e50").pack(side="left", padx=(0, 10))
        
        self.uid_entry = tk.Entry(entry_frame, font=("Arial", 20), 
                                 width=25, justify="center")
        self.uid_entry.pack(side="left")
        self.uid_entry.bind('<Return>', lambda e: self.scan_rfid())
        
        # Buttons
        btn_frame = tk.Frame(self, bg="#2c3e50")
        btn_frame.pack(pady=30)
        
        tk.Button(btn_frame, text="SCAN", font=("Arial", 18, "bold"),
                  bg="#27ae60", fg="white", width=12, height=2,
                  command=self.scan_rfid).grid(row=0, column=0, padx=20)
        
        tk.Button(btn_frame, text="CLEAR", font=("Arial", 18, "bold"),
                  bg="#e74c3c", fg="white", width=12, height=2,
                  command=self.clear).grid(row=0, column=1, padx=20)
        
        # Status display
        self.status_label = tk.Label(self, text="Ready for RFID scan", 
                                    font=("Arial", 14), fg="#3498db", bg="#2c3e50")
        self.status_label.pack(pady=20)
        
        # Instructions
        tk.Label(self, text="System Flow:", font=("Arial", 16, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(40, 10))
        
        flow_text = """
        1. Scan RFID Card
        2. New users: Choose Guest or Register
        3. Existing users: Proceed to Menu
        4. Insert coins to add balance
        5. Choose Water or Charging mode
        6. Complete transaction and logout
        """
        
        tk.Label(self, text=flow_text, font=("Arial", 12),
                 fg="#bdc3c7", bg="#2c3e50", justify="left").pack()
    
    def scan_rfid(self):
        uid = self.uid_entry.get().strip().upper()
        
        if not uid:
            self.status_label.config(text="Please enter RFID UID", fg="#e74c3c")
            return
        
        self.status_label.config(text="Scanning...", fg="#f39c12")
        self.update()
        
        # Check if user exists
        user_data = self.firebase.get_user_by_rfid(uid)
        
        if user_data:
            # Existing user - login
            self.app.session.login(user_data)
            self.status_label.config(text=f"Welcome {user_data['name']}!", fg="#2ecc71")
            self.uid_entry.delete(0, tk.END)
            
            # Proceed to main menu after delay
            self.after(1000, lambda: self.app.show_screen('main_menu'))
        else:
            # New user - show registration options
            self.status_label.config(text="New RFID detected", fg="#3498db")
            self.ask_user_type(uid)
    
    def ask_user_type(self, uid):
        """Ask new user if they want to register or proceed as guest"""
        response = messagebox.askyesno("New User Detected",
                                      "RFID not registered.\n\n"
                                      "Do you want to register as a member?\n"
                                      "(No = Proceed as Guest)")
        
        if response:
            # Show registration screen
            self.app.screens['register'].set_rfid_uid(uid)
            self.app.show_screen('register')
        else:
            # Create guest account
            guest_data = self.firebase.create_guest_user(uid)
            if guest_data:
                self.app.session.login(guest_data)
                self.status_label.config(text="Guest account created", fg="#2ecc71")
                self.uid_entry.delete(0, tk.END)
                self.after(1000, lambda: self.app.show_screen('main_menu'))
            else:
                self.status_label.config(text="Error creating account", fg="#e74c3c")
    
    def clear(self):
        self.uid_entry.delete(0, tk.END)
        self.status_label.config(text="Ready for RFID scan", fg="#3498db")
    
    def on_show(self):
        """Called when screen is shown"""
        self.clear()
        self.uid_entry.focus_set()