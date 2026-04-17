function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (value) => (value * Math.PI) / 180;
  const earthRadiusKm = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return earthRadiusKm * c;
}

function persistEta(activityId, distanceKm, chip) {
  return fetch(`/activities/${activityId}/eta`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      distance_km: distanceKm,
    }),
  }).then(async (response) => {
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Unable to save ETA");
    }
    chip.className = `eta-chip gps-chip status-${payload.eta_status}`;
    chip.textContent = payload.eta_label;
  });
}

function requestCurrentPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Browser geolocation unavailable"));
      return;
    }

    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 10000,
    });
  });
}

function initializeGpsPanels() {
  document.querySelectorAll(".gps-panel").forEach((panel) => {
    if (panel.dataset.bound === "true") {
      return;
    }
    panel.dataset.bound = "true";

    const button = panel.querySelector(".gps-button");
    const chip = panel.querySelector(".gps-chip");
    const venueLat = Number(panel.dataset.venueLat);
    const venueLng = Number(panel.dataset.venueLng);
    const activityId = panel.dataset.activityId;

    if (!button || !chip || Number.isNaN(venueLat) || Number.isNaN(venueLng) || !activityId) {
      return;
    }

    button.addEventListener("click", async () => {
      chip.textContent = "Checking location...";

      try {
        const position = await requestCurrentPosition();
        const distanceKm = haversineKm(
          position.coords.latitude,
          position.coords.longitude,
          venueLat,
          venueLng,
        );
        chip.textContent = "Saving ETA...";
        await persistEta(activityId, distanceKm, chip);
      } catch (error) {
        chip.textContent = error.message || "Unable to update ETA";
      }
    });
  });
}

async function searchLocationOnce(query) {
  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.searchParams.set("format", "jsonv2");
  url.searchParams.set("limit", "1");
  url.searchParams.set("q", query);

  const response = await fetch(url.toString(), {
    headers: {
      Accept: "application/json",
    },
  });
  if (!response.ok) {
    throw new Error("Search service unavailable");
  }

  const results = await response.json();
  if (!Array.isArray(results) || results.length === 0) {
    throw new Error("No map result found for that location");
  }
  return results[0];
}

function long2tile(lon, zoom) {
  return ((lon + 180) / 360) * 2 ** zoom;
}

function lat2tile(lat, zoom) {
  const rad = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * 2 ** zoom;
}

function renderStaticMapPreview(container, lat, lng) {
  if (!container || Number.isNaN(lat) || Number.isNaN(lng)) {
    return;
  }

  const zoom = 14;
  const xTile = long2tile(lng, zoom);
  const yTile = lat2tile(lat, zoom);
  const baseX = Math.floor(xTile);
  const baseY = Math.floor(yTile);
  const fracX = xTile - baseX;
  const fracY = yTile - baseY;
  const tileSize = 256;

  container.innerHTML = "";

  const grid = document.createElement("div");
  grid.className = "map-tile-grid";

  for (let y = -1; y <= 1; y += 1) {
    for (let x = -1; x <= 1; x += 1) {
      const tile = document.createElement("img");
      tile.className = "map-tile";
      tile.alt = "";
      tile.loading = "lazy";
      tile.src = `https://tile.openstreetmap.org/${zoom}/${baseX + x}/${baseY + y}.png`;
      grid.appendChild(tile);
    }
  }

  const pin = document.createElement("div");
  pin.className = "map-pin";
  pin.textContent = "●";
  pin.style.left = `${((fracX + 1) * tileSize / (tileSize * 3)) * 100}%`;
  pin.style.top = `${((fracY + 1) * tileSize / (tileSize * 3)) * 100}%`;

  container.appendChild(grid);
  container.appendChild(pin);
}

function updateLocationPreview(picker, lat, lng) {
  const form = picker.closest("form");
  const latInput = form?.querySelector("[data-location-lat]");
  const lngInput = form?.querySelector("[data-location-lng]");
  const preview = picker.querySelector("[data-location-map-preview]");
  const feedback = picker.querySelector("[data-location-feedback]");

  if (!latInput || !lngInput || !preview) {
    return;
  }

  latInput.value = String(lat);
  lngInput.value = String(lng);
  renderStaticMapPreview(preview, lat, lng);
  if (feedback) {
    feedback.textContent = `Pinned coordinates: ${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  }
}

function initializeLocationPickers() {
  document.querySelectorAll("[data-location-picker]").forEach((picker) => {
    if (picker.dataset.bound === "true") {
      return;
    }
    picker.dataset.bound = "true";

    const form = picker.closest("form");
    const input = form?.querySelector("[data-location-input]");
    const latInput = form?.querySelector("[data-location-lat]");
    const lngInput = form?.querySelector("[data-location-lng]");
    const searchButton = picker.querySelector("[data-location-search]");
    const feedback = picker.querySelector("[data-location-feedback]");

    if (!form || !input || !latInput || !lngInput || !searchButton) {
      return;
    }

    const initialLat = Number(latInput.value || picker.dataset.defaultLat);
    const initialLng = Number(lngInput.value || picker.dataset.defaultLng);
    if (!Number.isNaN(initialLat) && !Number.isNaN(initialLng)) {
      updateLocationPreview(picker, initialLat, initialLng);
    }

    searchButton.addEventListener("click", async () => {
      const query = input.value.trim();
      if (!query) {
        if (feedback) {
          feedback.textContent = "Type a location first, then search.";
        }
        return;
      }

      searchButton.disabled = true;
      searchButton.textContent = "Searching...";
      if (feedback) {
        feedback.textContent = "Searching OpenStreetMap...";
      }
      try {
        const result = await searchLocationOnce(query);
        const lat = Number(result.lat);
        const lng = Number(result.lon);
        if (Number.isNaN(lat) || Number.isNaN(lng)) {
          throw new Error("Search result is missing coordinates");
        }
        input.value = result.display_name || input.value;
        updateLocationPreview(picker, lat, lng);
      } catch (error) {
        if (feedback) {
          feedback.textContent = error.message || "Unable to find that location";
        }
      } finally {
        searchButton.disabled = false;
        searchButton.textContent = "Find on map";
      }
    });
  });
}

function initializeStaticMaps() {
  document.querySelectorAll("[data-static-map]").forEach((container) => {
    const lat = Number(container.dataset.lat);
    const lng = Number(container.dataset.lng);
    renderStaticMapPreview(container, lat, lng);
  });
}

function initializeDynamicRoles() {
  document.querySelectorAll("[data-dynamic-roles]").forEach((builder) => {
    const addButton = builder.querySelector("[data-add-role]");
    const rowsContainer = builder.querySelector("[data-role-rows]");

    if (!addButton || !rowsContainer || builder.dataset.bound === "true") {
      return;
    }
    builder.dataset.bound = "true";

    addButton.addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "role-row";
      row.setAttribute("data-role-row", "");
      row.innerHTML = `
        <input type="text" name="role_name" placeholder="Role name">
        <select name="role_type">
          <option>Mandatory</option>
          <option selected>Preferred</option>
          <option>Optional</option>
        </select>
        <input type="number" name="role_needed" min="1" value="1" aria-label="needed count">
        <button type="button" class="role-remove" data-remove-role aria-label="Remove role">×</button>
      `;
      rowsContainer.appendChild(row);
    });

    rowsContainer.addEventListener("click", (event) => {
      const target = event.target.closest("[data-remove-role]");
      if (!target) return;
      const row = target.closest("[data-role-row]");
      if (row) row.remove();
    });
  });
}

function initializeApp() {
  initializeGpsPanels();
  initializeLocationPickers();
  initializeStaticMaps();
  initializeDynamicRoles();
}

initializeApp();
