# Application and CV workspace

An opportunity offers two distinct actions: **Prepare application** opens or
reuses the user's CareerGPS workspace, while **Apply on employer site** opens a
trusted direct employer/ATS URL when one is available. Opening that URL does not
mark the application as submitted.

The workspace uses only source-held job information. Missing fields stay unknown;
there is no on-demand vacancy enrichment. The workspace provides CV tailoring,
cover-letter generation, notes, and explicit application status management.

Generated CVs use confirmed candidate facts, optional uploaded/pasted CV text,
semantic fact selection, controlled rewriting, numeric integrity checks, and a
selected layout. Users can change the document layout without regenerating facts.
The Gemini client stays alive for the entire generation request and is closed
afterward; retryable provider failures can use the configured fallback key.
