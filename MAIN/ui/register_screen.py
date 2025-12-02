import tkinter as tk
from tkinter import messagebox
import firebase_helpers

class RegisterScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        self.firebase = firebase_helpers
        self.rfid_uid = ""
        
        self.setup_ui()
    
    def setup_ui(self):
        # Header
        tk.Label(self, text="NEW USER REGISTRATION", font=("Arial", 28, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(40, 20))
        
        # Form frame
        form_frame = tk.Frame(self, bg="#34495e", padx=30, pady=30)
        form_frame.pack(pady=20)
        
        # RFID UID (read-only)
        tk.Label(form_frame, text="RFID UID:", font=("Arial", 14),
                 fg="white", bg="#34495e").grid(row=0, column=0, sticky="e", padx=10, pady=10)
        self.uid_label = tk.Label(form_frame, text="", font=("Arial", 14, "bold"),
                                  fg="#f39c12", bg="#34495e")
        self.uid_label.grid(row=0, column=1, padx=10, pady=10)
        
        # Student ID
        tk.Label(form_frame, text="Student ID:", font=("Arial", 14),
                 fg="white", bg="#34495e").grid(row=1, column=0, sticky="e", padx=10, pady=10)
        self.student_id_entry = tk.Entry(form_frame, font=("Arial", 14), width=25)
        self.student_id_entry.grid(row=1, column=1, padx=10, pady=10)
        
        # Name
        tk.Label(form_frame, text="Full Name:", font=("Arial", 14),
                 fg="white", bg="#34495e").grid(row=2, column=0, sticky="e", padx=10, pady=10)
        self.name_entry = tk.Entry(form_frame, font=("Arial", 14), width=25)
        self.name_entry.grid(row=2, column=1, padx=10, pady=10)
        
        # Initial Balance
        tk.Label(form_frame, text="Initial Balance:", font=("Arial", 14),
                 fg="white", bg="#34495e").grid(row=3, column=0, sticky="e", padx=10, pady=10)
        self.balance_entry = tk.Entry(form_frame, font=("Arial", 14), width=25)
        self.balance_entry.insert(0, "0.00")
        self.balance_entry.grid(row=3, column=1, padx=10, pady=10)
        
        # Buttons
        btn_frame = tk.Frame(self, bg="#2c3e50")
        btn_frame.pack(pady=30)
        
        tk.Button(btn_frame, text="REGISTER", font=("Arial", 18, "bold"),
                  bg="#27ae60", fg="white", width=15, height=2,
                  command=self.register).grid(row=0, column=0, padx=20)
        
        tk.Button(btn_frame, text="CANCEL", font=("Arial", 18, "bold"),
                  bg="#e74c3c", fg="white", width=15, height=2,
                  command=self.cancel).grid(row=0, column=1, padx=20)
        
        # Status
        self.status_label = tk.Label(self, text="", font=("Arial", 12),
                                    fg="#3498db", bg="#2c3e50")
        self.status_label.pack(pady=10)
    
    def set_rfid_uid(self, uid):
        """Set RFID UID from account screen"""
        self.rfid_uid = uid
        self.uid_label.config(text=uid[:8] + "...")
    
    def register(self):
        """Register new member"""
        # Get form data
        student_id = self.student_id_entry.get().strip()
        name = self.name_entry.get().strip()
        balance_str = self.balance_entry.get().strip()
        
        # Validation
        if not student_id:
            messagebox.showerror("Error", "Please enter Student ID")
            return
        
        if not name:
            messagebox.showerror("Error", "Please enter Full Name")
            return
        
        try:
            initial_balance = float(balance_str)
        except ValueError:
            messagebox.showerror("Error", "Invalid balance amount")
            return
        
        # Register user
        user_data = self.firebase.register_member(
            self.rfid_uid, name, student_id, initial_balance
        )
        
        if user_data:
            # Login and proceed to menu
            self.app.session.login(user_data)
            self.status_label.config(text="Registration successful!", fg="#2ecc71")
            
            # Clear form
            self.clear_form()
            
            # Proceed to main menu
            self.after(1500, lambda: self.app.show_screen('main_menu'))
        else:
            messagebox.showerror("Error", "Registration failed. RFID may already be registered.")
    
    def cancel(self):
        """Cancel registration and return to account screen"""
        self.clear_form()
        self.app.show_screen('account')
    
    def clear_form(self):
        """Clear all form fields"""
        self.student_id_entry.delete(0, tk.END)
        self.name_entry.delete(0, tk.END)
        self.balance_entry.delete(0, tk.END)
        self.balance_entry.insert(0, "0.00")
        self.status_label.config(text="")
        self.rfid_uid = ""
    
    def on_show(self):
        """Called when screen is shown"""
        if self.rfid_uid:
            self.student_id_entry.focus_set()