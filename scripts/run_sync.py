import asyncio
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.session import AsyncSessionLocal, create_db
from bot.services.rhinotech_sheet_reader import RhinotechSheetReader
from bot.services.branch_service import BranchService
from database.models import Branch, Laptop, LaptopBrand, BranchInventory
from sqlalchemy import select, func

async def run():
    print("Connecting to DB and creating tables...")
    await create_db()
    
    async with AsyncSessionLocal() as session:
        print("Starting live sync from Google Sheets...")
        res = await RhinotechSheetReader.sync_sheet_to_database(session)
        print("Sync completed successfully:", res)
        
        branches = (await session.scalars(select(Branch).where(Branch.is_active.is_(True)))).all()
        print(f"\nActive branches ({len(branches)}):")
        for b in branches:
            inv_sum = await session.scalar(
                select(func.sum(BranchInventory.quantity)).where(BranchInventory.branch_id == b.id)
            ) or 0
            print(f"  {b.name} ({b.code}) - Phone: {b.phone} - Total Stock: {inv_sum} laptops")
            
        brands = (await session.scalars(select(LaptopBrand))).all()
        print(f"\nBrands in DB ({len(brands)}):", [b.name for b in brands])
        
        total_laptops = await session.scalar(select(func.count(Laptop.id)))
        print(f"\nTotal laptops in DB: {total_laptops}")

if __name__ == "__main__":
    asyncio.run(run())
