# Phi AI Design System - Implementation Notes

## Overview
Complete UI redesign with Apple.com-inspired premium design system, including subtle holographic accents and smooth animations.

## Global Design System
**Location:** `static/styles.css`

### Key Features
- Premium neutral color palette
- Consistent spacing scale (8px base)
- Apple-like typography (SF Pro Display, Inter)
- Subtle holographic effects (holo-sheen, holo-border, glass, glow)
- Smooth animations (fadeInUp, card hover, button shimmer, input glow)
- Reduced motion support (`@media (prefers-reduced-motion: reduce)`)
- Responsive design (mobile, tablet, desktop)

### Component Classes
- **Buttons:** `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-subtle`, `.btn-danger`
- **Forms:** `.form-group`, `.form-label`, `.form-input`, `.form-textarea`, `.form-select`, `.form-helper`, `.form-error`
- **Cards:** `.card`, `.card-header`, `.card-body`, `.card-footer`
- **Badges:** `.badge`, `.badge-success`, `.badge-warning`, `.badge-error`, `.badge-neutral`
- **Alerts:** `.alert`, `.alert-success`, `.alert-warning`, `.alert-error`, `.alert-info`
- **Layout:** `.app-container`, `.main-content`, `.main-content-narrow`, `.section`
- **Holographic:** `.holo-sheen`, `.holo-border`, `.glass`, `.glow`
- **Animations:** `.enter`, `.enter-delay-1`, `.enter-delay-2`, `.enter-delay-3`

## Templates Updated

### ✅ Completed (13/19)
1. `index.html` - Landing page with hero section ✅
2. `login.html` - Clean login form ✅
3. `connect_apps.html` - Settings page with status badges ✅
4. `signup.html` - Signup form with dynamic organization fields ✅
5. `account_home.html` - Dashboard ✅
6. `account_meetings.html` - Meeting list ✅
7. `forgot_password.html` - Password reset request ✅
8. `reset_password.html` - Password reset form ✅
9. `set_username.html` - Username setting ✅
10. `upload_success.html` - Success page ✅
11. `connect_dropbox_confirm.html` - OAuth confirmation ✅
12. `connect_googledrive_confirm.html` - OAuth confirmation ✅
13. `connect_box_confirm.html` - OAuth confirmation ✅

### 🔄 Remaining (6/19)
14. `account.html` - Account settings (complex with JS dropdowns)
15. `enroll.html` - Voice enrollment (complex with audio recording JS)
16. `record_meeting.html` - Upload/record page (complex with lots of JS)
17. `add_members.html` - Add members page
18. `add_organization.html` - Add organization page
19. `edit_positions.html` - Edit positions page

## Base Template
**Location:** `templates/base.html`

Created a base template for consistency, though templates currently don't use Jinja inheritance. All templates include the same header/footer structure manually.

## Preserved Functionality

### Critical IDs (DO NOT RENAME)
- `organizationsContainer` - Signup form dynamic org fields
- `orgCount` - Hidden input for org count
- `addOrgBtn` - Add organization button
- `org-types-data` - JSON data for org types
- `org-directory-data` - JSON data for org directory
- `org_dropbtn_*`, `org_dropdown_*`, `org_search_*` - Dynamic org dropdown IDs
- `org_name_*`, `org_address_*` - Hidden org field inputs
- `confirmModal` - Modal confirmation dialogs
- `confirmOk`, `confirmCancel` - Modal buttons
- All form field `name` attributes preserved exactly
- All `onclick` handlers preserved

### Form Fields (DO NOT CHANGE)
- All `name` attributes on inputs, selects, textareas
- All `id` attributes used by JavaScript
- All hidden inputs and CSRF tokens
- All form `action` and `method` attributes

## Holographic Effects

### Implementation
- **Holo Sheen:** Subtle moving highlight on buttons/cards (`.holo-sheen`)
- **Holo Border:** Gradient border on key cards (`.holo-border`)
- **Glass Effect:** Frosted glass cards (`.glass`)
- **Glow:** Ambient glow behind hero sections (`.glow`)

### Usage
- Primary buttons have holo gradient overlay
- Status badges have subtle holo sheen
- Key cards can use holo-border class
- Hero sections can use glow effect

## Animations

### Page Load
- Content fades in and slides up (`.enter` class)
- Staggered delays for multiple elements

### Interactions
- Card hover: lifts 2px with shadow increase
- Button hover: shimmer effect on primary buttons
- Input focus: soft glow ring
- Status badge: subtle pulse on success badges

### Performance
- All animations use CSS transforms (GPU accelerated)
- Duration: 160-260ms
- Easing: cubic-bezier for smooth feel
- Reduced motion support disables all animations

## Next Steps

1. Update remaining templates to use global CSS
2. Apply holo effects to key UI elements
3. Add enter animations to page content
4. Test all forms and JavaScript functionality
5. Verify responsive design on all breakpoints
6. Test reduced motion support

## Notes for Developers

- **DO NOT** rename any IDs used by JavaScript
- **DO NOT** change form field `name` attributes
- **DO NOT** remove hidden inputs or CSRF tokens
- **DO** use global CSS classes instead of inline styles
- **DO** preserve all `onclick` handlers and event listeners
- **DO** test functionality after UI changes
