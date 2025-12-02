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
        self.cup_detected = False
        
        self.setup_ui()
        self.start_cup_check()
    
    def setup_ui(self):
        # Back button
        back_btn = tk.Button(self, text="BACK TO MENU", font=("Arial", 14),
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
        self.cost_label = tk.Label(display_frame, text="0.00", 
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
                                  state="disabled")
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
        
        # Instructions
        instructions = tk.Label(self, text="Insert coins first, then place cup, then start", 
                               font=("Arial", 12), fg="#bdc3c7", bg="#2c3e50")
        instructions.pack(pady=20)
    
    def start_cup_check(self):
        """Start periodic cup checking"""
        self.check_cup()
        self.after(1000, self.start_cup_check)
    
    def check_cup(self):
        """Check cup presence"""
        if self.dispensing:
            return
        
        # In real system, this would come from Arduino
        # For now, we'll update from Arduino messages via set_cup_status
        pass
    
    def set_cup_status(self, has_cup):
        """Set cup status from Arduino"""
        self.cup_detected = has_cup
        if has_cup:
            self.cup_status.config(text="Cup ready", fg="#2ecc71")
            if self.has_balance():
                self.start_btn.config(state="normal")
        else:
            self.cup_status.config(text="Place cup under dispenser", fg="#e74c3c")
            self.start_btn.config(state="disabled")
    
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
    
    def update_balance_display(self):
        """Update balance display"""
        if self.app.session.current_user:
            if self.app.session.is_guest():
                balance = self.app.session.coins_inserted
                self.balance_label.config(text=f"{balance:.2f}")
            else:
                balance = self.app.session.current_user['water_balance']
                self.balance_label.config(text=f"{balance:.2f}")
    
    def add_flow_pulses(self, pulses):
        """Add flow pulses from Arduino"""
        if self.dispensing:
            self.pulse_count += pulses
            self.update_display()
    
    def update_display(self):
        """Update display with current values"""
        liters = self.pulse_count / Config.PULSES_PER_LITER
        self.total_cost = liters * Config.WATER_PRICE_PER_LITER
        
        self.liters_label.config(text=f"{liters:.2f} L")
        self.cost_label.config(text=f"{self.total_cost:.2f}")
        
        # Check balance for members
        if self.app.session.is_member():
            balance = self.app.session.current_user['water_balance']
            if balance <= self.total_cost:
                self.stop_water()
    
    def start_water(self):
        """Start water dispensing"""
        if not self.cup_detected:
            messagebox.showerror("Error", "Please place a cup first!")
            return
        
        if not self.has_balance():
            messagebox.showerror("Error", "No balance available!")
            return
        
        # Send start command to Arduino
        success = self.app.send_to_arduino("PUMP:ON")
        if not success:
            messagebox.showwarning("Warning", "Cannot communicate with Arduino. Starting simulation.")
        
        self.dispensing = True
        self.pulse_count = 0
        self.total_cost = 0.00
        
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal" if self.app.session.is_member() else "disabled")
        
        # Start monitoring thread (for simulation if Arduino fails)
        self.monitor_thread = threading.Thread(target=self.simulate_flow)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        # For guests: auto-stop when coins run out
        if self.app.session.is_guest():
            self.auto_stop_for_guest()
    
    def simulate_flow(self):
        """Simulate flow if Arduino not available"""
        while self.dispensing:
            # In real system, pulses come from Arduino
            # For simulation, add small amount
            self.pulse_count += 2
            self.update_display()
            time.sleep(0.1)
    
    def auto_stop_for_guest(self):
        """Auto-stop for guest when coins run out"""
        if self.app.session.is_guest():
            coins = self.app.session.coins_inserted
            max_cost = coins
            max_liters = max_cost / Config.WATER_PRICE_PER_LITER
            max_pulses = max_liters * Config.PULSES_PER_LITER
            
            def check():
                if self.dispensing:
                    # Calculate current cost
                    liters = self.pulse_count / Config.PULSES_PER_LITER
                    current_cost = liters * Config.WATER_PRICE_PER_LITER
                    
                    if current_cost >= max_cost or self.pulse_count >= max_pulses:
                        self.stop_water()
                    else:
                        self.after(100, check)
            
            self.after(100, check)
    
    def stop_water(self):
        """Stop water dispensing"""
        if not self.dispensing:
            return
        
        # Send stop command to Arduino
        self.app.send_to_arduino("PUMP:OFF")
        
        self.dispensing = False
        
        # Calculate final cost
        liters = self.pulse_count / Config.PULSES_PER_LITER
        final_cost = liters * Config.WATER_PRICE_PER_LITER
        
        # Update user balance
        if self.app.session.current_user:
            if self.app.session.is_member():
                # Deduct from water balance
                new_balance = self.app.session.current_user['water_balance'] - final_cost
                self.app.session.current_user['water_balance'] = max(0, new_balance)
                
                # Update Firebase
                if hasattr(self.app, 'firebase') and self.app.firebase:
                    self.app.firebase.update_user_balance(
                        self.app.session.current_user['id'],
                        water_balance=self.app.session.current_user['water_balance']
                    )
                
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
                          f"Dispensed {liters:.2f}L\nCost: {final_cost:.2f}")
    
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
    
    def update_coin_display(self):
        """Update display when coins are inserted"""
        self.update_balance_display()
        if self.cup_detected and self.has_balance():
            self.start_btn.config(state="normal")
    
    def on_show(self):
        """Called when screen is shown"""
        self.update_balance_display()
        
        # Check if user is logged in
        if not self.app.session.current_user:
            messagebox.showerror("Error", "Please login first")
            self.app.show_screen('account')
            return
        
        # Reset display
        self.liters_label.config(text="0.00 L")
        self.cost_label.config(text="0.00")
        
        # Update button states
        if self.cup_detected and self.has_balance():
            self.start_btn.config(state="normal")
        else:
            self.start_btn.config(state="disabled")
        
        self.stop_btn.config(state="disabled")