import customtkinter as ctk
import requests
from config import BACKEND_URL

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class LibraryDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("DUM-E Autonomous Library Dashboard")
        self.geometry("700x500")

        # State
        self.current_member = None
        self.issued_books = []

        # --- UI Layout ---
        self.grid_columnconfigure(0, weight=1)

        # Header
        self.header = ctk.CTkLabel(self, text="🤖 DUM-E Station Control", font=("Roboto", 24, "bold"))
        self.header.grid(row=0, column=0, padx=20, pady=20)

        # Member Info Frame
        self.info_frame = ctk.CTkFrame(self)
        self.info_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        self.info_frame.grid_columnconfigure(0, weight=1)

        self.member_label = ctk.CTkLabel(self.info_frame, text="Waiting for Card Scan...", font=("Roboto", 16))
        self.member_label.grid(row=0, column=0, padx=20, pady=10)

        self.books_label = ctk.CTkLabel(self.info_frame, text="Issued Books: None", font=("Roboto", 14))
        self.books_label.grid(row=1, column=0, padx=20, pady=10)

        # Book Selection Frame
        self.book_frame = ctk.CTkFrame(self)
        self.book_frame.grid(row=2, column=0, padx=20, pady=10, sticky="ew")
        self.book_frame.grid_columnconfigure(1, weight=1)

        self.book_label = ctk.CTkLabel(self.book_frame, text="Select Book:", font=("Roboto", 14))
        self.book_label.grid(row=0, column=0, padx=10, pady=10)

        self.book_dropdown = ctk.CTkComboBox(self.book_frame, values=["Loading..."], width=300)
        self.book_dropdown.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        self.book_search = ctk.CTkEntry(self.book_frame, placeholder_text="Search book title...")
        self.book_search.grid(row=0, column=2, padx=10, pady=10)
        self.book_search.bind("<KeyRelease>", self.filter_books)

        # Control Frame
        self.ctrl_frame = ctk.CTkFrame(self)
        self.ctrl_frame.grid(row=3, column=0, padx=20, pady=20)

        self.issue_btn = ctk.CTkButton(self.ctrl_frame, text="Issue Book", command=self.request_issue,
                                      fg_color="green", hover_color="darkgreen", width=150, height=50)
        self.issue_btn.grid(row=0, column=0, padx=20, pady=20)

        self.return_btn = ctk.CTkButton(self.ctrl_frame, text="Return Book", command=self.request_return,
                                       fg_color="red", hover_color="darkred", width=150, height=50)
        self.return_btn.grid(row=0, column=1, padx=20, pady=20)

        # Testing Bypass Button
        self.test_btn = ctk.CTkButton(self, text="Test Login (Bypass Scan)", command=self.force_test_login,
                                     fg_color="gray", width=200, height=30)
        self.test_btn.grid(row=4, column=0, padx=20, pady=0)

        # Status Log
        self.status_log = ctk.CTkTextbox(self, height=120)
        self.status_log.grid(row=5, column=0, padx=20, pady=20, sticky="ew")
        self.log("System Ready. Please scan I-Card or use Test Login.")

        # State
        self.all_books = {} # Store {code: title}
        self.update_member_info()
        self.load_catalog()

        # Start polling for member data
        self.update_member_info()

    def log(self, msg):
        self.status_log.insert("end", f"> {msg}\n")
        self.status_log.see("end")

    def update_member_info(self):
        """Backend se check karta hai ki kaunsa member authenticated hai."""
        try:
            response = requests.get(f"{BACKEND_URL}/api/stats", timeout=1)
            if response.status_code == 200:
                data = response.json()
                member = data.get('current_member')
                if member:
                    self.current_member = member
                    self.member_label.configure(text=f"Welcome, {member['name']} ({member['id']})", text_color="green")
                    books = data.get('issued_books', [])
                    self.books_label.configure(text=f"Issued Books: {', '.join(books) if books else 'None'}")
                else:
                    # Agar backend me koi nahi hai, toh default dikhaye ya scan ka wait kare
                    if not self.current_member:
                        self.member_label.configure(text="Waiting for Card Scan...", text_color="white")
                        self.books_label.configure(text="Issued Books: None")
        except Exception as e:
            pass

        self.after(2000, self.update_member_info)

    def force_test_login(self):
        """Bypass for testing: Manually set a member if scan is not working."""
        self.current_member = {'id': 'S2500023', 'name': 'Test User'}
        self.member_label.configure(text=f"Welcome, Test User (S2500023)", text_color="yellow")
        self.log("Test Login active (Bypassed Scan)")


    def load_catalog(self):
        """Backend se books ki list lata hai aur dropdown update karta hai."""
        try:
            res = requests.get(f"{BACKEND_URL}/api/books", timeout=2)
            if res.status_code == 200:
                data = res.json()
                # Handle both dictionary {code: {title:...}} and list [{code:..., title:...}]
                if isinstance(data, list):
                    self.all_books = {b['code']: b for b in data}
                elif isinstance(data, dict):
                    self.all_books = data
                else:
                    self.all_books = {}

                self.update_dropdown(self.all_books)
        except Exception as e:
            self.log(f"Catalog load error: {e}")

    def update_dropdown(self, books):
        """Dropdown list ko update karta hai: 'CODE - Title' format mein."""
        values = [f"{code} - {data['title']}" for code, data in books.items()]
        self.book_dropdown.configure(values=values)
        if values:
            self.book_dropdown.set(values[0])

    def filter_books(self, event):
        """Search box ke basis par dropdown filter karta hai."""
        query = self.book_search.get().lower()
        filtered = [f"{code} - {data['title']}" for code, data in self.all_books.items()
                    if query in data['title'].lower() or query in code.lower()]

        if filtered:
            self.book_dropdown.configure(values=filtered)
            self.book_dropdown.set(filtered[0])
        else:
            self.book_dropdown.configure(values=["No books found"])
            self.book_dropdown.set("No books found")

    def get_selected_code(self):
        """Dropdown se sirf code (e.g. 'AC') extract karta hai."""
        val = self.book_dropdown.get()
        if " - " in val:
            return val.split(" - ")[0]
        return None

    def request_issue(self):
        if not self.current_member:
            self.log("Error: No member authenticated!")
            return

        book_code = self.get_selected_code()
        if not book_code:
            self.log("Error: Please select a valid book!")
            return

        self.log(f"Requesting issue of {book_code} for {self.current_member['id']}...")
        try:
            res = requests.post(f"{BACKEND_URL}/api/issue",
                                json={"book_code": book_code, "member_id": self.current_member['id']})
            if res.status_code == 200:
                self.log(f"Success: Robot moving to get {book_code}.")
            else:
                error_msg = res.json().get('detail', 'Unknown error')
                self.log(f"Failed: {error_msg}")
        except Exception as e:
            self.log(f"Error: {e}")

    def request_return(self):
        if not self.current_member:
            self.log("Error: No member authenticated!")
            return

        book_code = self.get_selected_code()
        if not book_code:
            self.log("Error: Please select a valid book!")
            return

        self.log(f"Requesting return of {book_code} for {self.current_member['id']}...")
        try:
            res = requests.post(f"{BACKEND_URL}/api/return",
                                json={"book_code": book_code, "member_id": self.current_member['id']})
            if res.status_code == 200:
                self.log(f"Success: Robot moving to collect {book_code}.")
            else:
                error_msg = res.json().get('detail', 'Unknown error')
                self.log(f"Failed: {error_msg}")
        except Exception as e:
            self.log(f"Error: {e}")

if __name__ == "__main__":
    app = LibraryDashboard()
    app.mainloop()
