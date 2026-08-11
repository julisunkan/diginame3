---
name: ID image privacy
description: Privacy requirement for uploaded identity-document images in the ID Validator.
---

The ID Validator must use an uploaded identity-document image only for the live analysis request. It must delete the image immediately after analysis, whether the request succeeds or fails, and must not retain a usable filesystem path in the validation record.

**Why:** The user explicitly requires that the app store no ID card images and communicate that deletion clearly to users.

**How to apply:** Preserve analysis results and text reports only; any future feature involving uploaded ID images must maintain immediate cleanup and avoid image previews or download routes.