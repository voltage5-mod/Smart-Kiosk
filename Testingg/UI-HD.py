import tkinter as tk

themes = {
    "Classic Maroon": {
        "bg": "#800000",   # maroon
        "fg": "#FFFFFF",   # white text
        "button_bg": "#5A0000",
        "button_fg": "#FFFFFF",
        "accent": "#E6E6E6"
    },
    "Modern Crimson": {
        "bg": "#8B0000",
        "fg": "#FFFFFF",
        "button_bg": "#B22222",
        "button_fg": "#FFFFFF",
        "accent": "#F2F2F2"
    },
    "Maroon-Gold Premium": {
        "bg": "#7A0010",
        "fg": "#FFFFFF",
        "button_bg": "#D4AF37",
        "button_fg": "#000000",
        "accent": "#D9D9D9"
    }
}

def apply_theme(theme):
    colors = themes[theme]
    root.config(bg=colors["bg"])
    label.config(bg=colors["bg"], fg=colors["fg"])
    for btn in buttons:
        btn.config(bg=colors["button_bg"], fg=colors["button_fg"], activebackground=colors["accent"])
    test_area.config(bg=colors["accent"])

root = tk.Tk()
root.title("Maroon UI Theme Tester")
root.geometry("500x350")

label = tk.Label(root, text="Select Theme", font=("Arial", 18, "bold"))
label.pack(pady=10)

buttons = []
for theme in themes:
    btn = tk.Button(root, text=theme, font=("Arial", 12, "bold"), 
                    command=lambda t=theme: apply_theme(t), width=22)
    btn.pack(pady=4)
    buttons.append(btn)

test_area = tk.Frame(root, height=120, width=400, bg="#E6E6E6")
test_area.pack(pady=15)

apply_theme("Classic Maroon")
root.mainloop()
