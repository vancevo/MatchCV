import os

from app.main import seed

if __name__ == "__main__":
    seed(os.getenv("SEED_OWNER_ID", "00000000-0000-0000-0000-000000000001"))
    print("Seed completed")
