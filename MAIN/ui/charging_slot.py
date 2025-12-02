import tkinter as tk
from tkinter import messagebox
import threading
import time
from config import Config

class ChargingSlot(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        self.current_slot = None
        self.charging = False
        self.start_time = 0
        self.elapsed_seconds = 0
        self.total_cost = 0.00
        self.return_countdown = 30
        
        self.setup_ui()
    
    def setup_ui(self):
        # Back button (disabled during charging)
        self.back_btn = tk.Button(self, text="← BACK", font=("Arial", 14),
                                command=self.return_to_charging_mode, 
                                bg="#7f8c8d", fg="white")
        self.back_btn.place(x=10, y=10)
        
        # Slot header
        self.slot_header = tk.Label(self, text="SLOT", font=("Arial", 28, "bold"),
                                   fg="white", bg="#2c3e50")
        self.slot_header.pack(pady=(20, 10))
        
        # Balance display
        balance_frame = tk.Frame(self, bg="#34495e", height=80)
        balance_frame.pack(fill="x", padx=100, pady=20)
        balance_frame.pack_propagate(False)
        
        tk.Label(balance_frame, text="Total Cost:", font=("Arial", 16),
                fg="white", bg="#34495e").place(relx=0.05, rely=0.3)
        
        self.cost_label = tk.Label(balance_frame, text="P0.00", 
                                  font=("Arial", 28, "bold"),
                                  fg="#2ecc71", bg="#34495e")
        self.cost_label.place(relx=0.3, rely=0.15)
        
        # Main display
        display_frame = tk.Frame(self, bg="black", height=180)
        display_frame.pack(pady=30, padx=100, fill="x")
        
        # Time display
        self.time_label = tk.Label(display_frame, text="00:00", 
                                  font=("Arial", 48, "bold"), fg="cyan", bg="black")
        self.time_label.pack(expand=True)
        
        # Status display
        self.status_label = tk.Label(display_frame, text="PLUG IN YOUR DEVICE", 
                                    font=("Arial", 18), fg="yellow", bg="black")
        self.status_label.pack(expand=True)
        
        # Current display
        current_frame = tk.Frame(self, bg="#2c3e50")
        current_frame.pack(pady=10)
        
        tk.Label(current_frame, text="Device Current:", font=("Arial", 14),
                fg="white", bg="#2c3e50").pack(side="left")
        self.current_value = tk.Label(current_frame, text="0.00A", 
                                     font=("Arial", 14, "bold"),
                                     fg="#3498db", bg="#2c3e50")
        self.current_value.pack(side="left", padx=10)
        
        # Control buttons
        control_frame = tk.Frame(self, bg="#2c3e50")
        control_frame.pack(pady=40)
        
        self.unlock_btn = tk.Button(control_frame, text="🔓 UNLOCK SLOT", 
                                   font=("Arial", 18, "bold"),
                                   bg="#e74c3c", fg="white", width=20, height=2,
                                   command=self.unlock_slot)
        self.unlock_btn.grid(row=0, column=0, padx=20)
        
        self.stop_btn = tk.Button(control_frame, text="STOP CHARGING", 
                                 font=("Arial", 18, "bold"),
                                 bg="#f39c12", fg="white", width=20, height=2,
                                 command=self.stop_charging,
                                 state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=20)
        
        # Return countdown
        self.return_label = tk.Label(self, text="", font=("Arial", 12),
                                    fg="#f39c12", bg="#2c3e50")
        
        # Start monitoring
        self.start_monitoring()
    
    def set_slot(self, slot):
        """Set the current slot"""
        self.current_slot = slot
        self.slot_header.config(text=f"SLOT {slot[-1]}")
        
        # Power on the slot
        self.app.hardware.relay_on(slot)
    
    def start_monitoring(self):
        """Start monitoring current"""
        self.check_current()
        self.after(1000, self.start_monitoring)
    
    def check_current(self):
        """Check device current"""
        if not self.current_slot:
            return
        
        # Read current
        current_data = self.app.hardware.read_current(self.current_slot)
        amps = current_data.get('amps', 0)
        
        # Update display
        self.current_value.config(text=f"{amps:.2f}A")
        
        # Check if device plugged in
        if not self.charging and amps > Config.CURRENT_THRESHOLD:
            self.start_charging()
        elif self.charging and amps < Config.CURRENT_THRESHOLD:
            self.device_unplugged()
    
    def start_charging(self):
        """Start charging process"""
        self.charging = True
        self.start_time = time.time()
        
        # Update status
        self.status_label.config(text="CHARGING...", fg="#2ecc71")
        self.unlock_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.back_btn.config(state="disabled")
        
        # Update Firebase
        if hasattr(self.app, 'firebase') and self.app.firebase:
            self.app.firebase.update_charging_status(
                self.app.session.current_user['id'],
                self.current_slot,
                'charging'
            )
        
        # Start timer
        self.update_timer()
    
    def device_unplugged(self):
        """Handle device unplug"""
        if self.charging:
            self.charging = False
            self.status_label.config(text="DEVICE UNPLUGGED", fg="#e74c3c")
            
            # Stop timer updates
            self.after_cancel(self.timer_update)
            
            # Auto-unlock after 30 seconds
            self.start_return_countdown()
    
    def update_timer(self):
        """Update charging timer"""
        if self.charging:
            self.elapsed_seconds = int(time.time() - self.start_time)
            
            # Format time
            minutes = self.elapsed_seconds // 60
            seconds = self.elapsed_seconds % 60
            self.time_label.config(text=f"{minutes:02d}:{seconds:02d}")
            
            # Calculate cost
            minutes_charged = self.elapsed_seconds / 60
            self.total_cost = (minutes_charged / 30) * Config.CHARGING_RATE
            self.cost_label.config(text=f"P{self.total_cost:.2f}")
            
            # Check balance
            balance = self.app.session.current_user['charge_balance']
            if balance <= self.total_cost:
                self.stop_charging()
                return
            
            # Schedule next update
            self.timer_update = self.after(1000, self.update_timer)
    
    def stop_charging(self):
        """Stop charging manually"""
        if not self.charging:
            return
        
        self.charging = False
        
        # Calculate final cost
        final_cost = min(self.total_cost, 
                        self.app.session.current_user['charge_balance'])
        
        # Deduct from balance
        self.app.session.current_user['charge_balance'] -= final_cost
        
        # Update Firebase
        if hasattr(self.app, 'firebase') and self.app.firebase:
            # Update balance
            self.app.firebase.update_user_balance(
                self.app.session.current_user['id'],
                charge_balance=self.app.session.current_user['charge_balance']
            )
            
            # Update status
            self.app.firebase.update_charging_status(
                self.app.session.current_user['id'],
                None,
                'idle'
            )
        
        # Power off slot
        self.app.hardware.relay_off(self.current_slot)
        
        # Unlock slot
        self.app.hardware.lock_slot(self.current_slot, lock=False)
        
        # Release slot from session
        self.app.session.release_slot()
        
        # Show completion
        minutes = self.elapsed_seconds // 60
        messagebox.showinfo("Charging Complete", 
                          f"Charged for {minutes} minutes\n"
                          f"Cost: P{final_cost:.2f}\n"
                          f"New balance: P{self.app.session.current_user['charge_balance']:.2f}")
        
        # Start return countdown
        self.start_return_countdown()
    
    def unlock_slot(self):
        """Unlock slot manually"""
        if not self.current_slot:
            return
        
        response = messagebox.askyesno("Unlock Slot", 
                                      "Are you sure you want to unlock this slot?\n"
                                      "Any charging in progress will stop.")
        
        if response:
            # Power off slot
            self.app.hardware.relay_off(self.current_slot)
            
            # Unlock slot
            self.app.hardware.lock_slot(self.current_slot, lock=False)
            
            # Release slot from session
            self.app.session.release_slot()
            
            # Update Firebase
            if hasattr(self.app, 'firebase') and self.app.firebase:
                self.app.firebase.update_charging_status(
                    self.app.session.current_user['id'],
                    None,
                    'idle'
                )
            
            # Return to charging mode
            self.return_to_charging_mode()
    
    def start_return_countdown(self):
        """Start 30-second return countdown"""
        self.return_countdown = 30
        self.return_label.pack(pady=10)
        self.update_return_countdown()
    
    def update_return_countdown(self):
        """Update return countdown"""
        if self.return_countdown > 0:
            self.return_label.config(
                text=f"Returning to charging mode in {self.return_countdown}s...")
            self.return_countdown -= 1
            self.after(1000, self.update_return_countdown)
        else:
            self.return_label.pack_forget()
            self.return_to_charging_mode()
    
    def return_to_charging_mode(self):
        """Return to charging mode selection"""
        # Clean up
        if self.current_slot:
            self.app.hardware.relay_off(self.current_slot)
        
        # Reset
        self.current_slot = None
        self.charging = False
        self.elapsed_seconds = 0
        self.total_cost = 0.00
        
        # Go back
        self.app.show_screen('charging_mode')
    
    def on_show(self):
        """Called when screen is shown"""
        if not self.current_slot:
            # No slot selected - go back
            self.app.show_screen('charging_mode')