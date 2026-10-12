# Test data

| File | Origin |
|---|---|
| `cut-in.xosc` | esmini `resources/xosc/cut-in.xosc`, tag v3.8.1, unchanged |
| `e6mini.xodr` | esmini `resources/xodr/e6mini.xodr`, tag v3.8.1, unchanged |
| `VehicleCatalog.xosc` | esmini `resources/xosc/Catalogs/Vehicles/VehicleCatalog.xosc`, tag v3.8.1, unchanged; needed by the scenarios below, because the image has no bundled catalogs |
| `endless.xosc` | derived from `cut-in.xosc`: the stop trigger fires only after 10⁹ s of simulated time, so the run ends only through the service's wall-clock timeout |
| `missing-road.xosc` | derived from `cut-in.xosc`: refers to `../xodr/does-not-exist.xodr` |

The esmini files are licensed under the Mozilla Public License 2.0 (https://github.com/esmini/esmini/blob/master/LICENSE).
The derived files remain under the MPL-2.0.
