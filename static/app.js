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

document.querySelectorAll(".gps-panel").forEach((panel) => {
  const button = panel.querySelector(".gps-button");
  const chip = panel.querySelector(".gps-chip");
  const venueLat = Number(panel.dataset.venueLat);
  const venueLng = Number(panel.dataset.venueLng);
  const activityId = panel.dataset.activityId;

  if (!button || !chip || Number.isNaN(venueLat) || Number.isNaN(venueLng) || !activityId) {
    return;
  }

  button.addEventListener("click", async () => {
    if (!navigator.geolocation) {
      chip.textContent = "Browser geolocation unavailable";
      return;
    }

    chip.textContent = "Checking location...";
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const distanceKm = haversineKm(
          position.coords.latitude,
          position.coords.longitude,
          venueLat,
          venueLng,
        );
        const eta = formatEtaMessage(distanceKm);
        chip.textContent = eta.text;

        try {
          const response = await fetch(`/activities/${activityId}/eta`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              eta_status: eta.status,
              eta_label: eta.text,
            }),
          });
          if (!response.ok) {
            const payload = await response.json();
            chip.textContent = payload.error || "Unable to save ETA";
            return;
          }
          chip.className = `eta-chip gps-chip status-${eta.status}`;
        } catch (_error) {
          chip.textContent = "Unable to reach the server";
        }
      },
      () => {
        chip.textContent = "Location permission denied";
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  });
});

document.querySelectorAll("[data-dynamic-roles]").forEach((builder) => {
  const addButton = builder.querySelector("[data-add-role]");
  const rowsContainer = builder.querySelector("[data-role-rows]");

  if (!addButton || !rowsContainer) {
    return;
  }

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
