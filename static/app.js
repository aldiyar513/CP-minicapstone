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

function googleMapsAvailable() {
  return Boolean(window.google && window.google.maps);
}

function buildInfoWindowContent(title, location) {
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

function routeEtaFromLeg(leg) {
  const durationSeconds = leg.duration?.value || 0;
  const durationMinutes = Math.max(1, Math.round(durationSeconds / 60));
  const distanceMeters = leg.distance?.value || 0;

  let status = "delayed";
  let prefix = "Delayed";
  if (distanceMeters < 200 || durationMinutes <= 2) {
    status = "checked_in";
    prefix = "At venue";
  } else if (durationMinutes <= 8) {
    status = "arriving_soon";
    prefix = "Arriving soon";
  } else if (durationMinutes <= 20) {
    status = "on_track";
    prefix = "On track";
  }

  const durationText = leg.duration?.text || `${durationMinutes} min`;
  const distanceText = leg.distance?.text ? ` • ${leg.distance.text}` : "";
  return {
    status,
    text: `${prefix} • ${durationText} away${distanceText}`,
  };
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

async function computeGoogleRouteEta(origin, destination) {
  const result = await new Promise((resolve, reject) => {
    const directionsService = new google.maps.DirectionsService();
    directionsService.route(
      {
        origin,
        destination,
        travelMode: google.maps.TravelMode.DRIVING,
      },
      (response, status) => {
        if (status !== "OK" || !response) {
          reject(new Error("Google Maps could not calculate a route"));
          return;
        }
        resolve(response);
      },
    );
  });
  const leg = result.routes?.[0]?.legs?.[0];
  if (!leg) {
    throw new Error("Unable to calculate a route");
  }
  return routeEtaFromLeg(leg);
}

async function computeFallbackEta(origin, destination) {
  const distanceKm = haversineKm(
    origin.lat,
    origin.lng,
    destination.lat,
    destination.lng,
  );
  return formatEtaMessage(distanceKm);
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
      chip.textContent = googleMapsAvailable() ? "Checking route with Google Maps..." : "Checking location...";

      try {
        const position = await requestCurrentPosition();
        const origin = {
          lat: position.coords.latitude,
          lng: position.coords.longitude,
        };
        const destination = { lat: venueLat, lng: venueLng };
        const eta = googleMapsAvailable()
          ? await computeGoogleRouteEta(origin, destination)
          : await computeFallbackEta(origin, destination);
        chip.textContent = eta.text;
        await persistEta(activityId, eta, chip);
      } catch (error) {
        chip.textContent = error.message || "Unable to update ETA";
      }
    });
  });
}

function ensureMapInstance(node, center, title, location) {
  if (!googleMapsAvailable()) {
    setMapPlaceholder(node, "Add GOOGLE_MAPS_API_KEY to show the pinned venue map.");
    return null;
  }

  clearMapPlaceholder(node);
  if (!node._map) {
    node._map = new google.maps.Map(node, {
      center,
      zoom: 14,
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: false,
    });
    node._marker = new google.maps.Marker({
      map: node._map,
      position: center,
      title,
    });
    node._infoWindow = new google.maps.InfoWindow({
      content: buildInfoWindowContent(title, location),
    });
    node._marker.addListener("click", () => {
      node._infoWindow.open(node._map, node._marker);
    });
  } else {
    node._map.setCenter(center);
    node._marker.setPosition(center);
    node._marker.setTitle(title);
    node._infoWindow.setContent(buildInfoWindowContent(title, location));
  }
  return node._map;
}

function initializeStaticMapCanvases() {
  document.querySelectorAll("[data-map-canvas]").forEach((node) => {
    const lat = Number(node.dataset.lat);
    const lng = Number(node.dataset.lng);
    if (Number.isNaN(lat) || Number.isNaN(lng)) {
      setMapPlaceholder(node, "No location pin available for this event yet.");
      return;
    }

    ensureMapInstance(
      node,
      { lat, lng },
      node.dataset.title || "Pinned venue",
      node.dataset.location || "",
    );
  });
}

function geocodeAddress(address) {
  return new Promise((resolve, reject) => {
    const geocoder = new google.maps.Geocoder();
    geocoder.geocode({ address }, (results, status) => {
      if (status !== "OK" || !results?.[0]?.geometry?.location) {
        reject(new Error("Google Maps could not pin that location"));
        return;
      }
      resolve(results[0]);
    });
  });
}

function updateLocationPickerMap(picker, lat, lng, title, location) {
  const mapNode = picker.querySelector("[data-location-map]");
  const form = picker.closest("form");
  const latInput = form?.querySelector("[data-location-lat]");
  const lngInput = form?.querySelector("[data-location-lng]");
  if (!mapNode || !latInput || !lngInput) {
    return;
  }

  latInput.value = String(lat);
  lngInput.value = String(lng);
  ensureMapInstance(mapNode, { lat, lng }, title, location);
}

function initializeLocationPickers() {
  document.querySelectorAll("[data-location-picker]").forEach((picker) => {
    const form = picker.closest("form");
    const input = form?.querySelector("[data-location-input]");
    const latInput = form?.querySelector("[data-location-lat]");
    const lngInput = form?.querySelector("[data-location-lng]");
    const mapNode = picker.querySelector("[data-location-map]");

    if (!input || !latInput || !lngInput || !mapNode) {
      return;
    }

    const currentLat = Number(latInput.value || picker.dataset.defaultLat);
    const currentLng = Number(lngInput.value || picker.dataset.defaultLng);
    if (!Number.isNaN(currentLat) && !Number.isNaN(currentLng)) {
      if (googleMapsAvailable()) {
        ensureMapInstance(mapNode, { lat: currentLat, lng: currentLng }, "Pinned venue", input.value || "Event location");
      } else {
        setMapPlaceholder(mapNode, "Add GOOGLE_MAPS_API_KEY to pin this venue on a map.");
      }
    }

    if (googleMapsAvailable() && !picker._autocomplete) {
      picker._autocomplete = new google.maps.places.Autocomplete(input, {
        fields: ["formatted_address", "geometry", "name"],
      });
      picker._autocomplete.addListener("place_changed", () => {
        const place = picker._autocomplete.getPlace();
        if (!place.geometry?.location) {
          return;
        }
        input.value = place.formatted_address || place.name || input.value;
        updateLocationPickerMap(
          picker,
          place.geometry.location.lat(),
          place.geometry.location.lng(),
          place.name || input.value,
          place.formatted_address || input.value,
        );
      });
    }

    if (picker.dataset.bound === "true") {
      return;
    }
    picker.dataset.bound = "true";

    input.addEventListener("blur", async () => {
      if (!googleMapsAvailable() || !input.value.trim()) {
        return;
      }
      try {
        const result = await geocodeAddress(input.value.trim());
        const point = result.geometry.location;
        updateLocationPickerMap(
          picker,
          point.lat(),
          point.lng(),
          result.formatted_address,
          result.formatted_address,
        );
      } catch (_error) {
        setMapPlaceholder(mapNode, "Google Maps could not pin that location yet.");
      }
    });
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

window.initGoogleMaps = function initGoogleMaps() {
  initializeApp();
};

initializeApp();
