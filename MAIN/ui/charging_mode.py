import tkinter as tk
from tkinter import messagebox
from config import Config

class ChargingMode(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        self.selected_slot = None
        
        self.setup_ui()
        self.start_slot_monitoring()
    
    def setup_ui(self):
        # Back button
        back_btn = tk.Button(self, text="← BACK TO MENU", font=("Arial", 14),
                           command=self.return_to_menu, bg="#7f8c8d", fg="white")
        back_btn.place(x=10, y=10)
        
        # Title
        tk.Label(self, text="DEVICE CHARGING", font=("Arial", 32, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(20, 10))
        
        # Balance display
        balance_frame = tk.Frame(self, bg="#34495e", height=80)
        balance_frame.pack(fill="x", padx=100, pady=20)
        balance_frame.pack_propagate(False)
        
        tk.Label(balance_frame, text="Charging Balance:", font=("Arial", 16),
                fg="white", bg="#34495e").place(relx=0.05, rely=0.3)
        
        self.balance_label = tk.Label(balance_frame, font=("Arial", 24, "bold"),
                                     fg="#2ecc71", bg="#34495e")
        self.balance_label.place(relx=0.35, rely=0.2)
        
        # Instructions
        tk.Label(self, text="INSERT COIN → SELECT SLOT → PLUG DEVICE → AUTO-START", 
                font=("Arial", 14), fg="#f39c12", bg="#2c3e50").pack(pady=10)
        
        tk.Label(self, text=f"Rate: P{Config.CHARGING_RATE:.2f} per 30 minutes",
                font=("Arial", 12), fg="#bdc3c7", bg="#2c3e50").pack(pady=5)
        
        # Slot selection grid (2x2)
        slots_frame = tk.Frame(self, bg="#2c3e50")
        slots_frame.pack(pady=40, padx=50)
        
        self.slot_buttons = {}
        slot_configs = [
            ("SLOT 1", "slot1", 0, 0),
            ("SLOT 2", "slot2", 0, 1),
            ("SLOT 3", "slot3", 1, 0),
            ("SLOT 4", "slot4", 1, 1)
        ]
        
        for title, slot, row, col in slot_configs:
            slot_frame = tk.LabelFrame(slots_frame, text=title, 
                                      font=("Arial", 20, "bold"),
                                      bg="#34495e", fg="white",
                                      width=400, height=250)
            slot_frame.grid(row=row, column=col, padx=20, pady=20, sticky="nsew")
            slot_frame.grid_propagate(False)
            
            # Status indicator
            status_frame = tk.Frame(slot_frame, bg="#34495e")
            status_frame.pack(pady=20)
            
            self.slot_buttons[f"{slot}_status"] = tk.Label(status_frame, text="AVAILABLE",
                                                          font=("Arial", 16, "bold"),
                                                          fg="#2ecc71", bg="#34495e")
            self.slot_buttons[f"{slot}_status"].pack()
            
            # Current display
            current_frame = tk.Frame(slot_frame, bg="#34495e")
            current_frame.pack(pady=10)
            
            tk.Label(current_frame, text="Current:", font=("Arial", 12),
                    fg="white", bg="#34495e").pack(side="left")
            self.slot_buttons[f"{slot}_current"] = tk.Label(current_frame, text="0.00A",
                                                           font=("Arial", 12, "bold"),
                                                           fg="#3498db", bg="#34495e")
            self.slot_buttons[f"{slot}_current"].pack(side="left", padx=5)
            
            # Select button
            select_btn = tk.Button(slot_frame, text="SELECT", 
                                  font=("Arial", 16, "bold"),
                                  bg="#3498db", fg="white", width=15,
                                  command=lambda s=slot: self.select_slot(s))
            select_btn.pack(pady=20)
            self.slot_buttons[f"{slot}_btn"] = select_btn
            
            # Occupied by label (hidden by default)
            self.slot_buttons[f"{slot}_occupied"] = tk.Label(slot_frame, text="",
                                                            font=("Arial", 10),
                                                            fg="#e74c3c", bg="#34495e")
    
    def start_slot_monitoring(self):
        """Start monitoring slot currents"""
        self.update_slot_displays()
        self.after(1000, self.start_slot_monitoring)
    
    def update_slot_displays(self):
        """Update all slot displays"""
        if self.app.session.current_user:
            # Update balance
            balance = self.app.session.current_user['charge_balance']
            self.balance_label.config(text=f"P{balance:.2f}")
        
        # Check each slot
        for slot in ['slot1', 'slot2', 'slot3', 'slot4']:
            # Read current
            current_data = self.app.hardware.read_current(slot)
            amps = current_data.get('amps', 0)
            
            # Update current display
            self.slot_buttons[f"{slot}_current"].config(text=f"{amps:.2f}A")
            
            # Check if slot is in use
            is_occupied = amps > Config.CURRENT_THRESHOLD
            
            if is_occupied:
                self.slot_buttons[f"{slot}_status"].config(text="IN USE", fg="#e74c3c")
                self.slot_buttons[f"{slot}_btn"].config(state="disabled", bg="#95a5a6")
            else:
                self.slot_buttons[f"{slot}_status"].config(text="AVAILABLE", fg="#2ecc71")
                
                # Enable button only if user has balance
                if self.app.session.current_user:
                    balance = self.app.session.current_user['charge_balance']
                    if balance >= Config.CHARGING_RATE:
                        self.slot_buttons[f"{slot}_btn"].config(state="normal", bg="#3498db")
                    else:
                        self.slot_buttons[f"{slot}_btn"].config(state="disabled", bg="#95a5a6")
    
    def select_slot(self, slot):
        """Select a charging slot"""
        if not self.app.session.current_user:
            messagebox.showerror("Error", "Please login first")
            return
        
        # Check balance
        balance = self.app.session.current_user['charge_balance']
        if balance < Config.CHARGING_RATE:
            messagebox.showerror("Insufficient Balance", 
                               f"Minimum P{Config.CHARGING_RATE:.2f} required")
            return
        
        # Check if slot is occupied
        current_data = self.app.hardware.read_current(slot)
        if current_data.get('amps', 0) > Config.CURRENT_THRESHOLD:
            messagebox.showerror("Slot Occupied", "This slot is currently in use")
            return
        
        # Unlock slot for 5 seconds
        self.app.hardware.lock_slot(slot, lock=False)
        
        # Update session
        self.app.session.occupy_slot(slot)
        
        # Update Firebase
        if hasattr(self.app, 'firebase') and self.app.firebase:
            self.app.firebase.update_charging_status(
                self.app.session.current_user['id'],
                slot,
                'waiting'
            )
        
        # Show instructions
        messagebox.showinfo("Slot Unlocked", 
                          f"Slot {slot[-1]} unlocked for 5 seconds.\n"
                          f"Plug in your device now.")
        
        # Re-lock after 5 seconds
        self.after(5000, lambda: self.relock_slot(slot))
        
        # Go to charging slot screen
        self.app.screens['charging_slot'].set_slot(slot)
        self.after(100, lambda: self.app.show_screen('charging_slot'))
    
    def relock_slot(self, slot):
        """Re-lock slot after timeout"""
        self.app.hardware.lock_slot(slot, lock=True)
    
    def return_to_menu(self):
        """Return to main menu"""
        self.app.show_screen('main_menu')
    
    def on_show(self):
        """Called when screen is shown"""
        self.update_slot_displays()