function formatEtaState(distanceKm) {
  if (distanceKm < 0.2) {
    return { status: "checked_in", label: "At venue" };
  }
  if (distanceKm < 2) {
    return { status: "arriving_soon", label: "About 3-6 min away" };
  }
  if (distanceKm < 5) {
    return { status: "on_track", label: "About 8-15 min away" };
  }
  return { status: "delayed", label: "More than 15 min away" };
}

function formatEtaMessage(distanceKm) {
  const eta = formatEtaState(distanceKm);
  const prefix = eta.status === "checked_in"
    ? "At venue"
    : eta.status === "arriving_soon"
      ? "Arriving soon"
      : eta.status === "on_track"
        ? "On track"
        : "Delayed";
  return { status: eta.status, text: `${prefix} • ${eta.label}` };
}

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

function leafletAvailable() {
  return Boolean(window.L);
}

function setMapPlaceholder(node, message) {
  if (!node) {
    return;
  }
  node.classList.add("map-canvas-placeholder");
  node.textContent = message;
}

function clearMapPlaceholder(node) {
  if (!node) {
    return;
  }
  node.classList.remove("map-canvas-placeholder");
  node.textContent = "";
}

function buildPopupContent(title, location) {
  const wrapper = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = title;
  wrapper.appendChild(heading);
  if (location) {
    wrapper.appendChild(document.createElement("br"));
    wrapper.appendChild(document.createTextNode(location));
  }
  return wrapper;
}

function ensureLeafletMap(node, center, zoom = 14) {
  if (!leafletAvailable()) {
    setMapPlaceholder(node, "Map library unavailable.");
    return null;
  }

  clearMapPlaceholder(node);
  if (!node._leafletMap) {
    node._leafletMap = L.map(node, {
      scrollWheelZoom: false,
    }).setView([center.lat, center.lng], zoom);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(node._leafletMap);
  } else {
    node._leafletMap.setView([center.lat, center.lng], zoom);
  }

  setTimeout(() => {
    node._leafletMap.invalidateSize();
  }, 0);

  return node._leafletMap;
}

function setLeafletMarker(node, center, title = "", location = "") {
  if (!node._leafletMap) {
    return;
  }

  if (!node._leafletMarker) {
    node._leafletMarker = L.marker([center.lat, center.lng]).addTo(node._leafletMap);
  } else {
    node._leafletMarker.setLatLng([center.lat, center.lng]);
  }

  if (title || location) {
    node._leafletMarker.bindPopup(buildPopupContent(title || "Pinned venue", location));
  }
}

function persistEta(activityId, eta, chip) {
  return fetch(`/activities/${activityId}/eta`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      eta_status: eta.status,
      eta_label: eta.text,
    }),
  }).then(async (response) => {
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || "Unable to save ETA");
    }
    chip.className = `eta-chip gps-chip status-${eta.status}`;
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
        const eta = formatEtaMessage(distanceKm);
        chip.textContent = eta.text;
        await persistEta(activityId, eta, chip);
      } catch (error) {
        chip.textContent = error.message || "Unable to update ETA";
      }
    });
  });
}

function initializeStaticMapCanvases() {
  document.querySelectorAll("[data-map-canvas]").forEach((node) => {
    const lat = Number(node.dataset.lat);
    const lng = Number(node.dataset.lng);
    const title = node.dataset.title || "Pinned venue";
    const location = node.dataset.location || "";

    if (Number.isNaN(lat) || Number.isNaN(lng)) {
      setMapPlaceholder(node, "No location pin available for this event yet.");
      return;
    }

    const map = ensureLeafletMap(node, { lat, lng });
    if (!map) {
      return;
    }
    setLeafletMarker(node, { lat, lng }, title, location);
  });
}

function updatePinnedLocation(picker, lat, lng, label) {
  const form = picker.closest("form");
  const mapNode = picker.querySelector("[data-location-map]");
  const latInput = form?.querySelector("[data-location-lat]");
  const lngInput = form?.querySelector("[data-location-lng]");
  const input = form?.querySelector("[data-location-input]");
  const feedback = picker.querySelector("[data-location-feedback]");

  if (!mapNode || !latInput || !lngInput) {
    return;
  }

  latInput.value = String(lat);
  lngInput.value = String(lng);
  if (feedback) {
    feedback.textContent = `Pinned coordinates: ${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  }

  const map = ensureLeafletMap(mapNode, { lat, lng });
  if (!map) {
    return;
  }

  const popupText = label || input?.value || "Pinned venue";
  setLeafletMarker(mapNode, { lat, lng }, "Pinned venue", popupText);
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
    const mapNode = picker.querySelector("[data-location-map]");
    const feedback = picker.querySelector("[data-location-feedback]");

    if (!form || !input || !latInput || !lngInput || !mapNode) {
      return;
    }

    const initialLat = Number(latInput.value || picker.dataset.defaultLat);
    const initialLng = Number(lngInput.value || picker.dataset.defaultLng);
    if (!Number.isNaN(initialLat) && !Number.isNaN(initialLng)) {
      updatePinnedLocation(picker, initialLat, initialLng, input.value || "Pinned venue");
    } else {
      setMapPlaceholder(mapNode, "Map unavailable for this venue.");
    }

    if (mapNode._leafletMap) {
      mapNode._leafletMap.on("click", (event) => {
        updatePinnedLocation(
          picker,
          event.latlng.lat,
          event.latlng.lng,
          input.value || "Pinned venue",
        );
      });
    }

    if (searchButton) {
      searchButton.addEventListener("click", async () => {
        const query = input.value.trim();
        if (!query) {
          if (feedback) {
            feedback.textContent = "Type a location first, then search once.";
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
          updatePinnedLocation(
            picker,
            lat,
            lng,
            result.display_name || input.value,
          );
        } catch (error) {
          if (feedback) {
            feedback.textContent = error.message || "Unable to find that location";
          }
        } finally {
          searchButton.disabled = false;
          searchButton.textContent = "Find on map";
        }
      });
    }
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
      `;
      rowsContainer.appendChild(row);
    });
  });
}

function initializeApp() {
  initializeGpsPanels();
  initializeStaticMapCanvases();
  initializeLocationPickers();
  initializeDynamicRoles();
}

initializeApp();
