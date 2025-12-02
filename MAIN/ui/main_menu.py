import tkinter as tk
from tkinter import messagebox
import time

class MainMenu(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        self.coin_update_interval = 1000  # ms
        
        self.setup_ui()
        self.start_updates()
    
    def setup_ui(self):
        # Status Bar (top)
        status_frame = tk.Frame(self, bg="#34495e", height=60)
        status_frame.pack(fill="x", padx=10, pady=10)
        status_frame.pack_propagate(False)
        
        # User info
        user_info = tk.Label(status_frame, font=("Arial", 12),
                            fg="white", bg="#34495e", anchor="w")
        user_info.pack(side="left", padx=20)
        
        # Balance display
        self.balance_label = tk.Label(status_frame, font=("Arial", 14, "bold"),
                                      fg="#2ecc71", bg="#34495e")
        self.balance_label.pack(side="left", padx=20)
        
        # UID display
        self.uid_label = tk.Label(status_frame, font=("Arial", 12),
                                 fg="#f39c12", bg="#34495e")
        self.uid_label.pack(side="left", padx=20)
        
        # Type indicator
        self.type_label = tk.Label(status_frame, font=("Arial", 12, "bold"),
                                  bg="#34495e")
        self.type_label.pack(side="right", padx=20)
        
        # Coin display
        coin_frame = tk.Frame(status_frame, bg="#34495e")
        coin_frame.pack(side="right", padx=20)
        
        tk.Label(coin_frame, text="Coins:", font=("Arial", 12),
                fg="white", bg="#34495e").pack(side="left")
        self.coin_label = tk.Label(coin_frame, text="P0.00", 
                                  font=("Arial", 12, "bold"), fg="#3498db", bg="#34495e")
        self.coin_label.pack(side="left", padx=5)
        
        # Main Title
        tk.Label(self, text="MAIN MENU", font=("Arial", 36, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(40, 20))
        
        # Welcome message
        self.welcome_label = tk.Label(self, font=("Arial", 20),
                                     fg="#ecf0f1", bg="#2c3e50")
        self.welcome_label.pack(pady=(0, 40))
        
        # Mode Selection Buttons
        mode_frame = tk.Frame(self, bg="#2c3e50")
        mode_frame.pack(pady=20)
        
        # Water Mode Button
        water_btn = tk.Button(mode_frame, text="WATER DISPENSER", 
                             font=("Arial", 24, "bold"),
                             bg="#3498db", fg="white", width=25, height=3,
                             command=self.goto_water)
        water_btn.grid(row=0, column=0, padx=30, pady=20)
        
        # Charging Mode Button
        charge_btn = tk.Button(mode_frame, text="DEVICE CHARGING", 
                              font=("Arial", 24, "bold"),
                              bg="#2ecc71", fg="white", width=25, height=3,
                              command=self.goto_charging)
        charge_btn.grid(row=0, column=1, padx=30, pady=20)
        
        # Unlock Slot Button (only visible when using a slot)
        self.unlock_frame = tk.Frame(self, bg="#2c3e50")
        
        tk.Label(self.unlock_frame, text="Currently using:", font=("Arial", 14),
                fg="white", bg="#2c3e50").pack(side="left", padx=(0, 10))
        
        self.slot_label = tk.Label(self.unlock_frame, font=("Arial", 14, "bold"),
                                  fg="#f39c12", bg="#2c3e50")
        self.slot_label.pack(side="left", padx=(0, 20))
        
        self.unlock_btn = tk.Button(self.unlock_frame, text="UNLOCK SLOT",
                                   font=("Arial", 12, "bold"),
                                   bg="#e74c3c", fg="white",
                                   command=self.unlock_slot)
        self.unlock_btn.pack(side="left")
        
        # Logout Button (bottom middle)
        logout_frame = tk.Frame(self, bg="#2c3e50")
        logout_frame.pack(side="bottom", pady=30)
        
        tk.Button(logout_frame, text="LOGOUT", font=("Arial", 16, "bold"),
                 bg="#7f8c8d", fg="white", width=20, height=2,
                 command=self.logout).pack()
    
    def start_updates(self):
        """Start periodic UI updates"""
        self.update_display()
        self.after(self.coin_update_interval, self.start_updates)
    
    def update_display(self):
        """Update all display elements"""
        if self.app.session.current_user:
            user = self.app.session.current_user
            
            # Welcome message
            self.welcome_label.config(text=f"Welcome, {user['name']}!")
            
            # Balance
            self.balance_label.config(text=self.app.session.get_balance_display())
            
            # UID
            self.uid_label.config(text=f"UID: {user['rfid_uid'][:8]}...")
            
            # User type
            if user['type'] == 'guest':
                self.type_label.config(text="GUEST", fg="#e74c3c")
            else:
                self.type_label.config(text="MEMBER", fg="#2ecc71")
            
            # Coins inserted
            self.coin_label.config(text=f"P{self.app.session.coins_inserted:.2f}")
            
            # Show unlock button if using a slot
            if user.get('occupied_slot'):
                self.slot_label.config(text=f"Slot {user['occupied_slot'][-1]}")
                self.unlock_frame.pack(pady=20)
            else:
                self.unlock_frame.pack_forget()
        else:
            # No user logged in - return to account screen
            self.app.show_screen('account')
    
    def goto_water(self):
        """Go to water mode"""
        self.app.show_screen('water_mode')
    
    def goto_charging(self):
        """Go to charging mode"""
        self.app.show_screen('charging_mode')
    
    def unlock_slot(self):
        """Unlock occupied slot"""
        user = self.app.session.current_user
        if user and user.get('occupied_slot'):
            slot = user['occupied_slot']
            
            # Unlock hardware
            self.app.hardware.lock_slot(slot, lock=False)
            
            # Release slot in session
            self.app.session.release_slot()
            
            # Update Firebase
            if self.app.firebase:
                self.app.firebase.update_charging_status(user['id'], None, 'idle')
            
            messagebox.showinfo("Slot Unlocked", f"Slot {slot[-1]} has been unlocked.")
            self.update_display()
    
    def logout(self):
        """Logout current user"""
        if self.app.session.current_user:
            # Release slot if occupied
            if self.app.session.slot_in_use:
                self.app.hardware.lock_slot(self.app.session.slot_in_use, lock=False)
            
            # Save session data if needed
            
            # Logout
            self.app.session.logout()
        
        # Return to account screen
        self.app.show_screen('account')
    
    def on_show(self):
        """Called when screen is shown"""
        self.update_display()