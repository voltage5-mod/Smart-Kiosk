import tkinter as tk
from tkinter import messagebox
import firebase_helpers

class AccountScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        
        # Initialize Firebase
        self.firebase = firebase_helpers.get_firebase_manager()
        if self.firebase:
            self.firebase.setup_database()
        
        self.current_user = None
        self.setup_ui()
        
    def setup_ui(self):
        # Header
        tk.Label(self, text="WATER AND CHARGING VENDO SYSTEM", font=("Arial", 28, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(20, 5))
        tk.Label(self, text="Enter RFID UID (type or paste) and press Scan", font=("Arial", 14),
                 fg="white", bg="#2c3e50").pack(pady=(0, 12))
        
        # RFID Entry
        self.uid_entry = tk.Entry(self, font=("Arial", 18), width=36)
        self.uid_entry.pack(pady=5)
        self.uid_entry.bind('<Return>', lambda event: self.scan())
        
        # Buttons
        btn_frame = tk.Frame(self, bg="#2c3e50")
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="Scan", font=("Arial", 16, "bold"),
                  bg="#27ae60", fg="white", width=14, command=self.scan).grid(row=0, column=0, padx=10)
        tk.Button(btn_frame, text="Clear", font=("Arial", 16, "bold"),
                  bg="#c0392b", fg="white", width=10, command=self.clear).grid(row=0, column=1, padx=10)
        
        # Status info
        self.info = tk.Label(self, text="", fg="white", bg="#2c3e50", font=("Arial", 12))
        self.info.pack(pady=(20,0))
        
        # User Info Display
        user_frame = tk.Frame(self, bg="#34495e", relief=tk.RAISED, borderwidth=2)
        user_frame.pack(pady=30, padx=50, fill="x")
        
        tk.Label(user_frame, text="ACCOUNT INFORMATION", font=("Arial", 16, "bold"),
                 fg="white", bg="#34495e").pack(pady=(10, 20))
        
        # User details
        details_frame = tk.Frame(user_frame, bg="#34495e")
        details_frame.pack(pady=(0, 20))
        
        tk.Label(details_frame, text="Name:", font=("Arial", 14), 
                 fg="white", bg="#34495e", width=10, anchor="e").grid(row=0, column=0, padx=10, pady=5)
        self.name_label = tk.Label(details_frame, text="Not logged in", font=("Arial", 14, "bold"),
                                   fg="#3498db", bg="#34495e", width=25, anchor="w")
        self.name_label.grid(row=0, column=1, padx=10, pady=5)
        
        tk.Label(details_frame, text="Balance:", font=("Arial", 14), 
                 fg="white", bg="#34495e", width=10, anchor="e").grid(row=1, column=0, padx=10, pady=5)
        self.balance_label = tk.Label(details_frame, text="Charge: 0.00 | Water: 0.00", font=("Arial", 14, "bold"),
                                      fg="#2ecc71", bg="#34495e", width=25, anchor="w")
        self.balance_label.grid(row=1, column=1, padx=10, pady=5)
        
        tk.Label(details_frame, text="RFID UID:", font=("Arial", 14), 
                 fg="white", bg="#34495e", width=10, anchor="e").grid(row=2, column=0, padx=10, pady=5)
        self.uid_label = tk.Label(details_frame, text="None", font=("Arial", 14, "bold"),
                                  fg="#f39c12", bg="#34495e", width=25, anchor="w")
        self.uid_label.grid(row=2, column=1, padx=10, pady=5)
        
        tk.Label(details_frame, text="Type:", font=("Arial", 14), 
                 fg="white", bg="#34495e", width=10, anchor="e").grid(row=3, column=0, padx=10, pady=5)
        self.type_label = tk.Label(details_frame, text="Not logged in", font=("Arial", 14, "bold"),
                                   fg="#e74c3c", bg="#34495e", width=25, anchor="w")
        self.type_label.grid(row=3, column=1, padx=10, pady=5)
        
        # Action Buttons
        action_frame = tk.Frame(self, bg="#2c3e50")
        action_frame.pack(pady=30)
        
        water_btn = tk.Button(action_frame, text="BUY WATER", font=("Arial", 20, "bold"),
                            bg="#3498db", fg="white", width=20, height=2,
                            command=self.goto_water)
        water_btn.grid(row=0, column=0, padx=20, pady=10)
        
        charge_btn = tk.Button(action_frame, text="CHARGE DEVICE", font=("Arial", 20, "bold"),
                             bg="#2ecc71", fg="white", width=20, height=2,
                             command=self.goto_charge)
        charge_btn.grid(row=0, column=1, padx=20, pady=10)
        
        # Admin button
        admin_btn = tk.Button(self, text="Admin", font=("Arial", 12),
                            bg="#7f8c8d", fg="white", width=10,
                            command=self.admin_mode)
        admin_btn.pack(pady=10)
    
    def scan(self):
        uid = self.uid_entry.get().strip()
        
        if not uid:
            self.info.config(text="Please enter RFID UID", fg="#e74c3c")
            return
        
        self.info.config(text="Scanning...", fg="#f39c12")
        self.update_idletasks()
        
        self.process_rfid(uid)
    
    def clear(self):
        self.uid_entry.delete(0, tk.END)
        self.info.config(text="")
        self.name_label.config(text="Not logged in")
        self.balance_label.config(text="Charge: 0.00 | Water: 0.00")
        self.uid_label.config(text="None")
        self.type_label.config(text="Not logged in")
        self.current_user = None
        self.app.session.logout()
    
    def process_rfid(self, uid):
        try:
            uid = uid.strip().upper().replace(" ", "")
            
            # Get user from Firebase
            user_data = self.firebase.get_user_by_rfid(uid)
            
            if user_data:
                # Login user
                self.app.session.login(user_data)
                self.current_user = user_data
                
                # Update UI
                self.name_label.config(text=user_data.get('name', 'Unknown'))
                
                charge_bal = user_data.get('charge_balance', 0)
                water_bal = user_data.get('water_balance', 0)
                balance_text = f"Charge: {charge_bal:.2f} | Water: {water_bal:.2f}"
                self.balance_label.config(text=balance_text)
                
                self.uid_label.config(text=uid[:8] + "..." if len(uid) > 8 else uid)
                
                user_type = user_data.get('type', 'guest')
                self.type_label.config(text=user_type.upper())
                if user_type == 'guest':
                    self.type_label.config(fg="#e74c3c")
                else:
                    self.type_label.config(fg="#2ecc71")
                
                # Clear entry and show success
                self.uid_entry.delete(0, tk.END)
                self.info.config(text=f"Welcome {user_data.get('name', 'User')}!", fg="#2ecc71")
                
                # Auto-focus on entry for next scan
                self.uid_entry.focus_set()
                
                # Proceed to main menu after delay
                self.after(1500, lambda: self.app.show_screen('main_menu'))
            else:
                # New user - ask for registration or guest
                self.info.config(text="New RFID detected", fg="#3498db")
                self.ask_user_type(uid)
                
        except Exception as e:
            self.info.config(text=f"Error: {str(e)}", fg="#e74c3c")
    
    def ask_user_type(self, uid):
        """Ask new user if they want to register or proceed as guest"""
        response = messagebox.askyesno("New User Detected",
                                      "RFID not registered.\n\n"
                                      "Do you want to register as a member?\n"
                                      "(No = Proceed as Guest)")
        
        if response:
            # Show registration screen
            self.show_registration(uid)
        else:
            # Create guest account
            guest_data = self.firebase.create_guest_user(uid)
            if guest_data:
                self.app.session.login(guest_data)
                self.current_user = guest_data
                self.info.config(text="Guest account created", fg="#2ecc71")
                self.uid_entry.delete(0, tk.END)
                
                # Update UI
                self.update_user_display(guest_data, uid)
                
                self.after(1500, lambda: self.app.show_screen('main_menu'))
            else:
                self.info.config(text="Error creating account", fg="#e74c3c")
    
    def show_registration(self, uid):
        """Show registration dialog"""
        from tkinter import simpledialog
        
        name = simpledialog.askstring("Registration", "Enter your full name:")
        if not name:
            self.info.config(text="Registration cancelled", fg="#e74c3c")
            return
        
        student_id = simpledialog.askstring("Registration", "Enter student ID (optional):")
        
        # Create member account
        member_data = self.firebase.register_member(uid, name, student_id or "")
        if member_data:
            self.app.session.login(member_data)
            self.current_user = member_data
            self.info.config(text=f"Welcome {name}!", fg="#2ecc71")
            self.uid_entry.delete(0, tk.END)
            
            # Update UI
            self.update_user_display(member_data, uid)
            
            self.after(1500, lambda: self.app.show_screen('main_menu'))
        else:
            self.info.config(text="Registration failed", fg="#e74c3c")
    
    def update_user_display(self, user_data, uid):
        """Update user display with data"""
        self.name_label.config(text=user_data.get('name', 'Unknown'))
        
        charge_bal = user_data.get('charge_balance', 0)
        water_bal = user_data.get('water_balance', 0)
        balance_text = f"Charge: {charge_bal:.2f} | Water: {water_bal:.2f}"
        self.balance_label.config(text=balance_text)
        
        self.uid_label.config(text=uid[:8] + "..." if len(uid) > 8 else uid)
        
        user_type = user_data.get('type', 'guest')
        self.type_label.config(text=user_type.upper())
        if user_type == 'guest':
            self.type_label.config(fg="#e74c3c")
        else:
            self.type_label.config(fg="#2ecc71")
    
    def goto_water(self):
        if self.app.session.current_user:
            self.app.show_screen('water_mode')
        else:
            self.info.config(text="Please scan RFID first!", fg="#e74c3c")
    
    def goto_charge(self):
        if self.app.session.current_user:
            self.app.show_screen('charging_mode')
        else:
            self.info.config(text="Please scan RFID first!", fg="#e74c3c")
    
    def admin_mode(self):
        # Open admin panel
        messagebox.showinfo("Admin", "Admin features coming soon!")
    
    def on_show(self):
        """Called when screen is shown"""
        self.clear()
        self.uid_entry.focus_set()