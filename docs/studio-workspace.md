# Studio workspace
The public task list remains /studio. /tasks is a compatibility redirect; task mutation and editor endpoints keep their existing URLs.

Both frontends read studio_tokens.css and studio_components.css. Server pages use studio_components.html for titles and workspace-section for content. workspace.css defines the production shell, responsive form/list layouts and grouped administrator navigation. workspace_features.css retains feature-specific controls for authentication, records and monitoring; it does not provide the retired page shell.

User pages covered: overview, accounts and scan dialog, task editor, runs, subscription, account centre, notifications, attention, help, login, registration, password recovery/change and errors.
Administrator pages covered: overview, users/customer detail, subscriptions, codes, tasks/group details, runs, operations, mail, audit and settings.
Version updates remain in /studio?view=updates and use the same heading component.

Do not restore tasks.html or studio_site.css. Keep API names, form field names, CSRF fields and feature data attributes stable when editing layouts.
Validation: test_studio_site.py checks rendered routes, headings, assets, unique IDs and workflow controls. Existing interaction harnesses cover menus, drawers, task selection, local state updates and mobile navigation. Visual browser verification remains manual.

