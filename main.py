"""
Kirana Connect — Single-file FastAPI application with embedded HTML frontend.
Run: python -m uvicorn main:app --host 0.0.0.0 --port 8000
"""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "database.db"

PRODUCTS = [
    ("Maggie Noodles", 14.0),
    ("Lays Chips", 20.0),
    ("Britannia Marie Gold", 30.0),
    ("Thums Up 250ml", 40.0),
    ("Heritage Milk 500ml", 28.0),
    ("Eggs", 72.0),
    ("Sugar 1kg", 45.0),
    ("Mysore Sandal Soap", 38.0),
    ("Dettol Liquid Handwash", 99.0),
    ("Kurkure", 10.0),
]

SHOPS = [
    ("SH001", "Padma kirana &General Store", "9546312548", 17.343305925265028, 78.31002646695285),
    ("SH002", "Srinivasa Kirana And General Store", "9531254648", 17.3435785913672, 78.30798187058397),
    ("SH003", "Sri Venkateswara Laxmi Kirana store", "9481254653", 17.3429070682502, 78.30934491605883),
]

STOCK_MATRIX = {
    "SH001": [True, True, True, False, True, True, True, False, True, True],
    "SH002": [True, False, True, True, True, False, True, True, True, False],
    "SH003": [False, True, True, True, True, True, False, True, False, True],
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS inventory;
        DROP TABLE IF EXISTS shops;
        CREATE TABLE shops (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL
        );
        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            in_stock INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );
        """
    )
    for shop in SHOPS:
        cur.execute(
            "INSERT INTO shops (id, name, phone, lat, lng) VALUES (?, ?, ?, ?, ?)",
            shop,
        )
    for shop_id, stock_row in STOCK_MATRIX.items():
        for (name, price), in_stock in zip(PRODUCTS, stock_row):
            cur.execute(
                "INSERT INTO inventory (shop_id, product_name, price, in_stock) VALUES (?, ?, ?, ?)",
                (shop_id, name, price, 1 if in_stock else 0),
            )
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Kirana Connect", lifespan=lifespan)


class StockUpdate(BaseModel):
    in_stock: bool


@app.get("/api/shops")
def list_shops():
    conn = get_db()
    rows = conn.execute("SELECT * FROM shops ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/shops/{shop_id}/inventory")
def shop_inventory(shop_id: str):
    conn = get_db()
    shop = conn.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
    if not shop:
        conn.close()
        raise HTTPException(status_code=404, detail="Shop not found")
    items = conn.execute(
        "SELECT id, product_name, price, in_stock FROM inventory WHERE shop_id = ? ORDER BY product_name",
        (shop_id,),
    ).fetchall()
    conn.close()
    return {
        "shop": dict(shop),
        "items": [
            {
                "id": r["id"],
                "product_name": r["product_name"],
                "price": r["price"],
                "in_stock": bool(r["in_stock"]),
            }
            for r in items
        ],
    }


@app.get("/api/search")
def search_products(q: str = ""):
    conn = get_db()
    query = f"%{q.strip().lower()}%"
    rows = conn.execute(
        """
        SELECT s.id, s.name, s.phone, s.lat, s.lng, i.product_name, i.price, i.in_stock
        FROM shops s
        JOIN inventory i ON s.id = i.shop_id
        WHERE LOWER(i.product_name) LIKE ? AND i.in_stock = 1
        ORDER BY s.name, i.product_name
        """,
        (query,),
    ).fetchall()
    conn.close()
    shops_map: dict = {}
    for r in rows:
        sid = r["id"]
        if sid not in shops_map:
            shops_map[sid] = {
                "id": sid,
                "name": r["name"],
                "phone": r["phone"],
                "lat": r["lat"],
                "lng": r["lng"],
                "products": [],
            }
        shops_map[sid]["products"].append({"name": r["product_name"], "price": r["price"]})
    return list(shops_map.values())


@app.post("/api/inventory/{item_id}/stock")
def update_stock(item_id: int, body: StockUpdate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE inventory SET in_stock = ? WHERE id = ?",
        (1 if body.in_stock else 0, item_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    row = conn.execute(
        "SELECT id, shop_id, product_name, price, in_stock FROM inventory WHERE id = ?",
        (item_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return {
        "id": row["id"],
        "shop_id": row["shop_id"],
        "product_name": row["product_name"],
        "price": row["price"],
        "in_stock": bool(row["in_stock"]),
    }


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Kirana Connect</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    .hidden { display: none !important; }
    .view-card { transition: transform 0.25s ease, box-shadow 0.25s ease; }
    .view-card:hover { transform: scale(1.04); }
    .toggle-switch { position: relative; width: 44px; height: 24px; }
    .toggle-switch input { opacity: 0; width: 0; height: 0; }
    .toggle-slider {
      position: absolute; cursor: pointer; inset: 0;
      background: #374151; border-radius: 24px; transition: 0.3s;
    }
    .toggle-slider::before {
      content: ""; position: absolute; height: 18px; width: 18px;
      left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.3s;
    }
    .toggle-switch input:checked + .toggle-slider { background: #10b981; }
    .toggle-switch input:checked + .toggle-slider::before { transform: translateX(20px); }
    @keyframes scanLine {
      0% { top: 8%; opacity: 0.4; }
      50% { opacity: 1; }
      100% { top: 88%; opacity: 0.4; }
    }
    .scan-line {
      position: absolute; left: 10%; right: 10%; height: 3px;
      background: linear-gradient(90deg, transparent, #f59e0b, #fbbf24, #f59e0b, transparent);
      box-shadow: 0 0 12px #f59e0b, 0 0 24px #fbbf24;
      animation: scanLine 1.6s ease-in-out infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .ai-spinner {
      width: 22px; height: 22px; border: 3px solid #7c3aed44;
      border-top-color: #a78bfa; border-radius: 50%; animation: spin 0.8s linear infinite;
    }
    #map { min-height: 320px; z-index: 0; }
  </style>
</head>
<body style="min-height: 100vh; background-color: #000000; color: #ffffff; font-family: system-ui, sans-serif;">

  <header style="position: sticky; top: 0; z-index: 50; border-bottom: 1px solid rgba(255,255,255,0.1); background-color: rgba(0,0,0,0.85); backdrop-filter: blur(8px);">
    <div style="max-width: 72rem; margin: 0 auto; padding: 1rem;">
      <h1 style="font-size: 1.5rem; font-weight: 700; color: #ffffff;">Kirana Connect</h1>
      <p style="font-size: 0.875rem; color: #9ca3af;">Your neighborhood kirana, connected</p>
    </div>
  </header>

  <main style="max-width: 72rem; margin: 0 auto; padding: 2rem 1rem;">

    <!-- LANDING VIEW -->
    <div id="view-landing" class="view-section">
      <div class="rounded-2xl border p-8 md:p-12" style="background-color: #0f0c1b !important; color: #ffffff; border-color: #4c1d95;">
        <h2 class="text-3xl font-bold text-center mb-2" style="color: #ddd6fe;">Welcome</h2>
        <p class="text-center mb-10" style="color: #a78bfa;">Choose how you'd like to continue</p>
        <div class="grid md:grid-cols-2 gap-6 max-w-3xl mx-auto">
          <button type="button" onclick="showView('customer')" class="view-card text-left rounded-xl border p-8"
            style="background-color: #1a162b !important; border-color: #8b5cf6; color: #ffffff; box-shadow: 0 0 20px rgba(139, 92, 246, 0.35);">
            <div class="text-4xl mb-4">🛒</div>
            <h3 class="text-xl font-semibold mb-2" style="color: #c4b5fd;">Enter as Customer</h3>
            <p class="text-sm" style="color: #a78bfa;">Search products across nearby kirana stores and find what's in stock.</p>
          </button>
          <button type="button" onclick="openMerchantLogin()" class="view-card text-left rounded-xl border p-8"
            style="background-color: #1a162b !important; border-color: #8b5cf6; color: #ffffff; box-shadow: 0 0 20px rgba(139, 92, 246, 0.35);">
            <div class="text-4xl mb-4">🏪</div>
            <h3 class="text-xl font-semibold mb-2" style="color: #c4b5fd;">Enter as Merchant Portal</h3>
            <p class="text-sm" style="color: #a78bfa;">Manage inventory, update stock status, and run smart pricing tools.</p>
          </button>
        </div>
      </div>
    </div>

    <!-- CUSTOMER VIEW -->
    <div id="view-customer" class="view-section hidden">
      <div class="rounded-2xl border p-6 md:p-8" style="background-color: #0d1117 !important; color: #ffffff; border-color: #164e63;">
        <div class="flex items-center justify-between mb-6">
          <h2 class="text-2xl font-bold" style="color: #cffafe;">Find Products Nearby</h2>
          <button type="button" onclick="showView('landing')" class="text-sm px-4 py-2 rounded-lg border font-medium"
            style="border-color: #06b6d4 !important; color: #06b6d4 !important; background-color: transparent;">← Back</button>
        </div>
        <input id="search-input" type="text" placeholder="Search products (e.g. Maggie, Milk, Soap)..."
          class="w-full mb-6 px-4 py-3 rounded-xl focus:outline-none"
          style="background-color: #161b22 !important; border: 1px solid #06b6d4; color: #ffffff;"
          oninput="debouncedSearch()" />
        <div class="grid lg:grid-cols-2 gap-6">
          <div id="search-results" class="space-y-4 max-h-[480px] overflow-y-auto"></div>
          <div id="map" class="rounded-xl overflow-hidden" style="border: 1px solid #06b6d4;"></div>
        </div>
      </div>
    </div>

    <!-- MERCHANT VIEW -->
    <div id="view-merchant" class="view-section hidden">
      <div class="rounded-2xl border p-6 md:p-8" style="background-color: #121212 !important; color: #ffffff; border-color: #065f46;">
        <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h2 class="text-2xl font-bold" style="color: #10b981;">Merchant Dashboard</h2>
            <p id="merchant-shop-label" class="text-sm mt-1" style="color: #34d399;"></p>
          </div>
          <button type="button" onclick="merchantLogout()" class="text-sm px-4 py-2 rounded-lg border"
            style="border-color: #f43f5e; color: #f87171; background-color: transparent;">Logout</button>
        </div>

        <div class="flex flex-wrap gap-3 mb-6 p-4 rounded-xl border" style="background-color: #1e1e1e !important; border-color: #374151;">
          <button type="button" onclick="openAddItemModal()" class="px-4 py-2 rounded-lg border-2 font-medium text-sm"
            style="border-color: #10b981 !important; color: #10b981 !important; background-color: transparent;">+ Add New Item</button>
          <button type="button" onclick="openAiScanModal()" class="px-4 py-2 rounded-lg border font-medium text-sm flex items-center gap-2"
            style="background-color: rgba(245, 158, 11, 0.15); border-color: #f59e0b; color: #fcd34d;">
            <span>📷</span> ⚡ Quick Add via AI Scan
          </button>
          <button type="button" onclick="runAiPricing()" class="px-4 py-2 rounded-lg border font-medium text-sm"
            style="background-color: rgba(124, 58, 237, 0.15); border-color: #8b5cf6; color: #c4b5fd;">🤖 Run AI Smart Pricing</button>
        </div>

        <div id="ai-pricing-panel" class="hidden mb-6 p-4 rounded-xl border" style="background-color: #1e1e1e !important; border-color: #6d28d9;"></div>

        <div class="overflow-x-auto rounded-xl border" style="border-color: #374151;">
          <table class="w-full text-sm">
            <thead style="background-color: #1e1e1e !important; color: #10b981;">
              <tr>
                <th class="text-left px-4 py-3 font-medium">Product</th>
                <th class="text-left px-4 py-3 font-medium">Price (₹)</th>
                <th class="text-left px-4 py-3 font-medium">Status</th>
                <th class="text-left px-4 py-3 font-medium">Toggle</th>
              </tr>
            </thead>
            <tbody id="inventory-table"></tbody>
          </table>
        </div>
      </div>
    </div>

  </main>

  <!-- Merchant Login Modal -->
  <div id="merchant-login-modal" class="hidden fixed inset-0 z-[100] flex items-center justify-center" style="background-color: rgba(0,0,0,0.75);">
    <div class="rounded-2xl p-6 w-full max-w-md mx-4 shadow-2xl border" style="background-color: #1e1e1e !important; border-color: #10b981; color: #ffffff;">
      <h3 class="text-xl font-bold mb-4" style="color: #10b981;">Merchant Login</h3>
      <label class="block text-sm mb-1" style="color: #9ca3af;">Select Shop</label>
      <select id="login-shop-select" class="w-full mb-4 px-3 py-2 rounded-lg focus:outline-none"
        style="background-color: #121212 !important; border: 1px solid #10b981; color: #ffffff;"></select>
      <label class="block text-sm mb-1" style="color: #9ca3af;">Password</label>
      <input id="login-password" type="password" placeholder="Enter password" value="demo123"
        class="w-full mb-6 px-3 py-2 rounded-lg focus:outline-none"
        style="background-color: #121212 !important; border: 1px solid #10b981; color: #ffffff;" />
      <div class="flex gap-3">
        <button type="button" onclick="closeMerchantLogin()" class="flex-1 py-2 rounded-lg border" style="border-color: #4b5563; color: #d1d5db; background-color: transparent;">Cancel</button>
        <button type="button" onclick="merchantLogin()" class="flex-1 py-2 rounded-lg font-medium" style="background-color: #10b981 !important; color: #ffffff;">Login</button>
      </div>
    </div>
  </div>

  <!-- Add Item Modal -->
  <div id="add-item-modal" class="hidden fixed inset-0 z-[100] flex items-center justify-center" style="background-color: rgba(0,0,0,0.75);">
    <div class="rounded-2xl p-6 w-full max-w-md mx-4 shadow-2xl border" style="background-color: #1e1e1e !important; border-color: #10b981; color: #ffffff;">
      <h3 class="text-xl font-bold mb-4" style="color: #10b981;">Add New Item</h3>
      <label class="block text-sm mb-1" style="color: #9ca3af;">Product Name</label>
      <input id="add-item-name" type="text" placeholder="Product name"
        class="w-full mb-3 px-3 py-2 rounded-lg focus:outline-none"
        style="background-color: #121212 !important; border: 1px solid #10b981; color: #ffffff;" />
      <label class="block text-sm mb-1" style="color: #9ca3af;">Price (₹)</label>
      <input id="add-item-price" type="number" placeholder="0.00" step="0.01"
        class="w-full mb-6 px-3 py-2 rounded-lg focus:outline-none"
        style="background-color: #121212 !important; border: 1px solid #10b981; color: #ffffff;" />
      <div class="flex gap-3">
        <button type="button" onclick="closeAddItemModal()" class="flex-1 py-2 rounded-lg border" style="border-color: #4b5563; color: #d1d5db; background-color: transparent;">Cancel</button>
        <button type="button" onclick="mockAddItem()" class="flex-1 py-2 rounded-lg font-medium" style="background-color: #10b981 !important; color: #ffffff;">Add Item</button>
      </div>
    </div>
  </div>

  <!-- AI Scan Modal -->
  <div id="ai-scan-modal" class="hidden fixed inset-0 z-[100] flex items-center justify-center" style="background-color: rgba(0,0,0,0.75);">
    <div class="rounded-2xl p-6 w-full max-w-md mx-4 shadow-2xl border" style="background-color: #1e1e1e !important; border-color: #f59e0b;">
      <h3 class="text-xl font-bold mb-4 flex items-center gap-2" style="color: #fcd34d;"><span>📷</span> AI Product Scanner</h3>
      <div class="relative h-48 rounded-xl overflow-hidden mb-4" style="background-color: #000000; border: 2px solid #f59e0b;">
        <div class="absolute inset-0 flex items-center justify-center text-6xl" style="color: rgba(245,158,11,0.3);">📦</div>
        <div class="scan-line"></div>
        <p id="scan-status" class="absolute bottom-3 left-0 right-0 text-center text-sm" style="color: #fbbf24;">Scanning...</p>
      </div>
      <button type="button" onclick="closeAiScanModal()" class="w-full py-2 rounded-lg border" style="border-color: #4b5563; color: #d1d5db; background-color: transparent;">Close</button>
    </div>
  </div>

<script>
let map = null;
let markers = [];
let currentShopId = null;
let inventoryData = [];
let searchDebounce = null;
let aiRecommendations = null;

const BADGE_IN_STOCK = 'style="background-color: #064e3b !important; color: #34d399 !important; border: 1px solid #10b981;"';
const BADGE_OUT_STOCK = 'style="background-color: #4c0519 !important; color: #f87171 !important; border: 1px solid #f43f5e;"';

function showView(name) {
  document.querySelectorAll('.view-section').forEach(function(el) { el.classList.add('hidden'); });
  var target = document.getElementById('view-' + name);
  if (target) target.classList.remove('hidden');
  if (name === 'customer') {
    if (!map) initMap();
    doSearch('');
    setTimeout(function() { if (map) map.invalidateSize(); }, 200);
  }
}

function debouncedSearch() {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(function() {
    doSearch(document.getElementById('search-input').value);
  }, 250);
}

async function doSearch(q) {
  var res = await fetch('/api/search?q=' + encodeURIComponent(q || ''));
  var shops = await res.json();
  renderSearchResults(shops);
  updateMapMarkers(shops);
}

function renderSearchResults(shops) {
  var container = document.getElementById('search-results');
  if (!shops.length) {
    container.innerHTML = '<p class="text-center py-8" style="color: #6b7280;">No shops found with matching in-stock products.</p>';
    return;
  }
  container.innerHTML = shops.map(function(shop) {
    return '<div class="rounded-xl border p-4" style="background-color: #161b22 !important; border-color: #06b6d4; color: #ffffff;">' +
      '<div class="flex justify-between items-start mb-2">' +
        '<h3 class="font-semibold" style="color: #cffafe;">' + esc(shop.name) + '</h3>' +
        '<a href="tel:' + shop.phone + '" class="text-xs px-3 py-1 rounded-full font-medium" style="background-color: rgba(6, 182, 212, 0.15) !important; border: 1px solid #06b6d4; color: #06b6d4 !important;">📞 ' + esc(shop.phone) + '</a>' +
      '</div>' +
      '<ul class="space-y-1 mt-2">' +
        shop.products.map(function(p) {
          return '<li class="text-sm flex justify-between" style="color: #9ca3af;"><span>' + esc(p.name) + '</span><span style="color: #06b6d4;">₹' + p.price.toFixed(2) + '</span></li>';
        }).join('') +
      '</ul>' +
    '</div>';
  }).join('');
}

function initMap() {
  map = L.map('map').setView([17.3433, 78.3093], 16);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);
}

function updateMapMarkers(shops) {
  if (!map) return;
  markers.forEach(function(m) { map.removeLayer(m); });
  markers = [];
  shops.forEach(function(shop) {
    var m = L.marker([shop.lat, shop.lng]).addTo(map)
      .bindPopup('<b>' + esc(shop.name) + '</b><br><a href="tel:' + shop.phone + '">' + esc(shop.phone) + '</a>');
    markers.push(m);
  });
  if (shops.length) {
    var group = L.featureGroup(markers);
    map.fitBounds(group.getBounds().pad(0.15));
  }
}

async function loadShopsDropdown() {
  var res = await fetch('/api/shops');
  var shops = await res.json();
  var sel = document.getElementById('login-shop-select');
  sel.innerHTML = shops.map(function(s) {
    return '<option value="' + s.id + '">' + esc(s.name) + '</option>';
  }).join('');
}

function openMerchantLogin() {
  loadShopsDropdown();
  document.getElementById('merchant-login-modal').classList.remove('hidden');
}

function closeMerchantLogin() {
  document.getElementById('merchant-login-modal').classList.add('hidden');
  document.getElementById('login-password').value = 'demo123';
}

async function merchantLogin() {
  var shopId = document.getElementById('login-shop-select').value;
  currentShopId = shopId;
  closeMerchantLogin();
  await loadInventory(shopId);
  showView('merchant');
}

async function loadInventory(shopId) {
  var res = await fetch('/api/shops/' + shopId + '/inventory');
  var data = await res.json();
  inventoryData = data.items;
  document.getElementById('merchant-shop-label').textContent = data.shop.name;
  renderInventoryTable();
}

function renderInventoryTable() {
  var tbody = document.getElementById('inventory-table');
  tbody.innerHTML = inventoryData.map(function(item) {
    return '<tr style="background-color: #1e1e1e !important; border-top: 1px solid #374151;">' +
      '<td class="px-4 py-3" style="color: #e5e7eb;">' + esc(item.product_name) + '</td>' +
      '<td class="px-4 py-3" data-price-id="' + item.id + '" style="color: #10b981;">₹' + item.price.toFixed(2) + '</td>' +
      '<td class="px-4 py-3"><span id="badge-' + item.id + '" class="text-xs px-2 py-1 rounded-full" ' +
        (item.in_stock ? BADGE_IN_STOCK : BADGE_OUT_STOCK) + '>' +
        (item.in_stock ? 'Stock Available' : 'Out of Stock') + '</span></td>' +
      '<td class="px-4 py-3"><label class="toggle-switch">' +
        '<input type="checkbox" ' + (item.in_stock ? 'checked' : '') + ' onchange="toggleStock(' + item.id + ', this.checked)" />' +
        '<span class="toggle-slider"></span></label></td></tr>';
  }).join('');
}

async function toggleStock(itemId, inStock) {
  var res = await fetch('/api/inventory/' + itemId + '/stock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ in_stock: inStock })
  });
  if (res.ok) {
    var updated = await res.json();
    var idx = inventoryData.findIndex(function(i) { return i.id === itemId; });
    if (idx >= 0) inventoryData[idx] = updated;
    var badge = document.getElementById('badge-' + itemId);
    if (badge) {
      badge.textContent = updated.in_stock ? 'Stock Available' : 'Out of Stock';
      badge.className = 'text-xs px-2 py-1 rounded-full';
      badge.setAttribute('style', updated.in_stock
        ? 'background-color: #064e3b !important; color: #34d399 !important; border: 1px solid #10b981;'
        : 'background-color: #4c0519 !important; color: #f87171 !important; border: 1px solid #f43f5e;');
    }
  }
}

function merchantLogout() {
  currentShopId = null;
  inventoryData = [];
  aiRecommendations = null;
  document.getElementById('inventory-table').innerHTML = '';
  document.getElementById('merchant-shop-label').textContent = '';
  document.getElementById('ai-pricing-panel').classList.add('hidden');
  document.getElementById('ai-pricing-panel').innerHTML = '';
  closeMerchantLogin();
  closeAddItemModal();
  closeAiScanModal();
  document.getElementById('login-shop-select').innerHTML = '';
  document.getElementById('login-password').value = 'demo123';
  showView('landing');
}

function openAddItemModal() {
  document.getElementById('add-item-name').value = '';
  document.getElementById('add-item-price').value = '';
  document.getElementById('add-item-modal').classList.remove('hidden');
}

function closeAddItemModal() {
  document.getElementById('add-item-modal').classList.add('hidden');
}

function mockAddItem() {
  var name = document.getElementById('add-item-name').value.trim();
  var price = parseFloat(document.getElementById('add-item-price').value);
  if (!name || isNaN(price)) { alert('Please enter a valid product name and price.'); return; }
  alert('Mock: "' + name + '" at ₹' + price.toFixed(2) + ' would be added (presentation only).');
  closeAddItemModal();
}

function openAiScanModal() {
  document.getElementById('scan-status').textContent = 'Scanning...';
  document.getElementById('ai-scan-modal').classList.remove('hidden');
  setTimeout(function() {
    document.getElementById('scan-status').textContent = 'Product detected!';
    closeAiScanModal();
    openAddItemModal();
    document.getElementById('add-item-name').value = 'Bingo Chips';
    document.getElementById('add-item-price').value = '25.00';
  }, 2000);
}

function closeAiScanModal() {
  document.getElementById('ai-scan-modal').classList.add('hidden');
}

function runAiPricing() {
  var panel = document.getElementById('ai-pricing-panel');
  panel.classList.remove('hidden');
  panel.innerHTML = '<div class="flex items-center gap-3" style="color: #c4b5fd;">' +
    '<div class="ai-spinner"></div><span>[AI Agent Analyzing trends...]</span></div>';
  setTimeout(function() {
    aiRecommendations = { maggie: -2, thumsUp: 3 };
    panel.innerHTML = '<p class="mb-3" style="color: #ddd6fe;">Optimization Complete: Suggested lowering Maggie Noodles by ₹2 to clear excess stock, and raising Thums Up by ₹3 due to weekend peak demand!</p>' +
      '<button type="button" onclick="applyAiRecommendations()" class="px-4 py-2 rounded-lg text-sm font-medium" style="background-color: #7c3aed; color: #ffffff;">Apply AI Recommendations</button>';
  }, 2500);
}

function applyAiRecommendations() {
  if (!aiRecommendations) return;
  inventoryData.forEach(function(item) {
    if (item.product_name === 'Maggie Noodles') item.price = Math.max(1, item.price + aiRecommendations.maggie);
    if (item.product_name === 'Thums Up 250ml') item.price = item.price + aiRecommendations.thumsUp;
  });
  renderInventoryTable();
  document.getElementById('ai-pricing-panel').innerHTML += '<p class="text-sm mt-3" style="color: #10b981;">✓ Recommendations applied to displayed prices.</p>';
}

function esc(str) {
  var d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

document.getElementById('merchant-login-modal').addEventListener('click', function(e) {
  if (e.target.id === 'merchant-login-modal') closeMerchantLogin();
});
document.getElementById('add-item-modal').addEventListener('click', function(e) {
  if (e.target.id === 'add-item-modal') closeAddItemModal();
});
document.getElementById('ai-scan-modal').addEventListener('click', function(e) {
  if (e.target.id === 'ai-scan-modal') closeAiScanModal();
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


