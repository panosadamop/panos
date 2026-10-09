# Tool System

## Tool contract
Each tool declares:
- unique name/version;
- description;
- typed input/output schema;
- permission level/action class;
- supported platforms;
- side-effect classification;
- timeout/cancellation support;
- audit metadata;
- privacy implications.

## Initial tool families

### Applications
- discover applications
- launch application
- focus application/window
- open file/project in preferred IDE

### Filesystem
- list/read/search/create/update/move/delete with workspace safeguards

### Shell
- execute process with cwd/env/timeouts
- stream/capture output
- cancellation

### Git
- status/diff/log/branches
- create branch
- commit/push/merge only through approval policy

### Project analysis
- detect stack
- dependency inventory
- build/test discovery
- repository map

### Screen
- active-window metadata
- active-window screenshot
- screen screenshot when explicitly requested

### Browser
- launch/navigate/read/research
- no autonomous consequential submission/upload

### Email
- read/search/summarize/draft
- no send tool must exist in the default runtime

### Calendar
- read/free-busy
- proposed writes routed through approval

### Project management
- read Jira/Azure DevOps
- prepare changes
- execute writes only after approval

### Documents
- generate Markdown/DOCX/XLSX/PPTX/PDF

## Tool-result handling
Raw untrusted output must be clearly separated from system/user instructions before being provided to models.
