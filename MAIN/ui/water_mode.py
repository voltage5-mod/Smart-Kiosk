import tkinter as tk
from tkinter import messagebox
import threading
import time
from config import Config

class WaterMode(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg="#2c3e50")
        self.app = app
        self.dispensing = False
        self.pulse_count = 0
        self.total_cost = 0.00
        self.stop_countdown = 0
        
        self.setup_ui()
    
    def setup_ui(self):
        # Back button
        back_btn = tk.Button(self, text="← BACK TO MENU", font=("Arial", 14),
                           command=self.return_to_menu, bg="#7f8c8d", fg="white")
        back_btn.place(x=10, y=10)
        
        # Title
        tk.Label(self, text="WATER DISPENSER", font=("Arial", 32, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(20, 10))
        
        # Balance display
        balance_frame = tk.Frame(self, bg="#34495e", height=80)
        balance_frame.pack(fill="x", padx=100, pady=20)
        balance_frame.pack_propagate(False)
        
        tk.Label(balance_frame, text="Available Balance:", font=("Arial", 16),
                fg="white", bg="#34495e").place(relx=0.05, rely=0.3)
        
        self.balance_label = tk.Label(balance_frame, font=("Arial", 24, "bold"),
                                     fg="#2ecc71", bg="#34495e")
        self.balance_label.place(relx=0.4, rely=0.2)
        
        # Main display
        display_frame = tk.Frame(self, bg="black", height=180)
        display_frame.pack(pady=30, padx=100, fill="x")
        
        # Liters display
        self.liters_label = tk.Label(display_frame, text="0.00 L", 
                                    font=("Arial", 48, "bold"), fg="cyan", bg="black")
        self.liters_label.pack(expand=True)
        
        # Cost display
        self.cost_label = tk.Label(display_frame, text="P0.00", 
                                  font=("Arial", 36), fg="yellow", bg="black")
        self.cost_label.pack(expand=True)
        
        # User type info
        user_frame = tk.Frame(self, bg="#2c3e50")
        user_frame.pack(pady=10)
        
        if self.app.session.is_guest():
            tk.Label(user_frame, text="Guest User: Full water dispense only", 
                    font=("Arial", 14), fg="#e74c3c", bg="#2c3e50").pack()
        else:
            tk.Label(user_frame, text="Member: Stop button available", 
                    font=("Arial", 14), fg="#2ecc71", bg="#2c3e50").pack()
        
        # Control buttons
        control_frame = tk.Frame(self, bg="#2c3e50")
        control_frame.pack(pady=40)
        
        self.start_btn = tk.Button(control_frame, text="START WATER", 
                                  font=("Arial", 24, "bold"),
                                  bg="#27ae60", fg="white", width=15, height=2,
                                  command=self.start_water,
                                  state="normal" if self.has_balance() else "disabled")
        self.start_btn.grid(row=0, column=0, padx=20)
        
        self.stop_btn = tk.Button(control_frame, text="STOP", 
                                 font=("Arial", 24, "bold"),
                                 bg="#e74c3c", fg="white", width=15, height=2,
                                 command=self.stop_water,
                                 state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=20)
        
        # Cup status
        self.cup_status = tk.Label(self, text="No cup detected", 
                                  font=("Arial", 16), fg="#e74c3c", bg="#2c3e50")
        self.cup_status.pack(pady=10)
        
        # Stop countdown display (for members)
        self.countdown_label = tk.Label(self, text="", 
                                       font=("Arial", 14), fg="#f39c12", bg="#2c3e50")
        
        # Start cup detection
        self.check_cup_interval()
    
    def has_balance(self):
        """Check if user has water balance"""
        if not self.app.session.current_user:
            return False
        
        if self.app.session.is_guest():
            # Guest needs coins inserted
            return self.app.session.coins_inserted > 0
        else:
            # Member needs water balance
            return self.app.session.current_user['water_balance'] > 0
    
    def check_cup_interval(self):
        """Periodically check cup presence"""
        if self.dispensing:
            self.after(100, self.check_cup_interval)
            return
        
        # Check ultrasonic sensor
        has_cup = self.check_cup()
        
        if has_cup:
            self.cup_status.config(text="Cup ready", fg="#2ecc71")
            self.start_btn.config(state="normal" if self.has_balance() else "disabled")
        else:
            self.cup_status.config(text="Place cup under dispenser", fg="#e74c3c")
            self.start_btn.config(state="disabled")
        
        self.after(500, self.check_cup_interval)
    
    def check_cup(self):
        """Check if cup is present using ultrasonic"""
        # Implementation depends on your hardware
        # For now, simulate detection
        return True
    
    def start_water(self):
        """Start water dispensing"""
        if not self.check_cup():
            messagebox.showerror("Error", "Please place a cup first!")
            return
        
        if not self.has_balance():
            messagebox.showerror("Error", "No balance available!")
            return
        
        self.dispensing = True
        self.pulse_count = 0
        self.total_cost = 0.00
        
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal" if self.app.session.is_member() else "disabled")
        
        # Start Arduino pump
        self.app.hardware.relay_on('pump_relay')
        
        # Start flow monitoring
        self.monitor_thread = threading.Thread(target=self.monitor_flow)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        # For guests: auto-stop when coins run out
        if self.app.session.is_guest():
            self.auto_stop_for_guest()
    
    def monitor_flow(self):
        """Monitor water flow"""
        while self.dispensing:
            # Read flow sensor pulses (from Arduino or GPIO)
            # For simulation, add pulses
            self.pulse_count += 2
            
            # Calculate
            liters = self.pulse_count / Config.PULSES_PER_LITER
            self.total_cost = liters * Config.WATER_PRICE_PER_LITER
            
            # Update display
            self.liters_label.config(text=f"{liters:.2f} L")
            self.cost_label.config(text=f"P{self.total_cost:.2f}")
            
            # Check balance for members
            if self.app.session.is_member():
                balance = self.app.session.current_user['water_balance']
                if balance <= self.total_cost:
                    self.stop_water()
                    break
            
            time.sleep(0.1)
    
    def auto_stop_for_guest(self):
        """Auto-stop for guest when coins run out"""
        if self.app.session.is_guest():
            coins = self.app.session.coins_inserted
            max_liters = coins / Config.WATER_PRICE_PER_LITER
            max_pulses = max_liters * Config.PULSES_PER_LITER
            
            def check():
                if self.dispensing and self.pulse_count >= max_pulses:
                    self.stop_water()
                elif self.dispensing:
                    self.after(100, check)
            
            self.after(100, check)
    
    def stop_water(self):
        """Stop water dispensing"""
        if not self.dispensing:
            return
        
        self.dispensing = False
        
        # Stop pump
        self.app.hardware.relay_off('pump_relay')
        
        # Calculate final cost
        liters = self.pulse_count / Config.PULSES_PER_LITER
        final_cost = liters * Config.WATER_PRICE_PER_LITER
        
        # Update user balance
        if self.app.session.current_user:
            if self.app.session.is_member():
                # Deduct from water balance
                self.app.session.current_user['water_balance'] -= final_cost
                
                # Start 5-second countdown
                self.start_countdown()
            else:
                # Guest: use inserted coins
                self.app.session.coins_inserted = 0
        
        # Update display
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        
        # Show completion
        messagebox.showinfo("Complete", 
                          f"Dispensed {liters:.2f}L\nCost: P{final_cost:.2f}")
    
    def start_countdown(self):
        """Start 5-second countdown to return to menu"""
        self.stop_countdown = 5
        self.countdown_label.pack(pady=10)
        self.update_countdown()
    
    def update_countdown(self):
        """Update countdown display"""
        if self.stop_countdown > 0:
            self.countdown_label.config(
                text=f"Returning to menu in {self.stop_countdown}s...")
            self.stop_countdown -= 1
            self.after(1000, self.update_countdown)
        else:
            self.countdown_label.pack_forget()
            self.return_to_menu()
    
    def return_to_menu(self):
        """Return to main menu"""
        self.app.show_screen('main_menu')
    
    def on_show(self):
        """Called when screen is shown"""
        self.update_balance_display()
    
    def update_balance_display(self):
        """Update balance display"""
        if self.app.session.current_user:
            if self.app.session.is_guest():
                balance = self.app.session.coins_inserted
            else:
                balance = self.app.session.current_user['water_balance']
            
            self.balance_label.config(text=f"P{balance:.2f}")