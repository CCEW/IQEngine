# IQEngine metadata reference

This folder contains reference material for the IQEngine metadata catalog and the practical contract used when indexing external data sources such as MinIO, Azure Blob, or other S3-compatible storage.

## What IQEngine is doing

IQEngine is not a source-of-truth database for raw signal data. It is a catalog/index layer built on top of SigMF metadata files.

The sync process reads objects from external storage, looks for `.sigmf-meta` files, validates them, enriches them with IQEngine-specific traceability fields, and stores the resolved metadata document in MongoDB.

This means:

- the storage layer (for example MinIO or Azure Blob) is the source of truth for the actual files
- the MongoDB `IQEngine` database is a derived catalog/index of the available metadata
- the stable integration contract is the API plus the SigMF metadata format, not direct MongoDB writes

## Database and collection shape

IQEngine uses the MongoDB database named `IQEngine` and stores metadata documents in the `metadata` collection.

The catalog document is built from a SigMF payload and then extended with fields such as:

- `global.traceability:origin.account`
- `global.traceability:origin.container`
- `global.traceability:origin.file_path`
- `global.traceability:revision`
- `global.traceability:sample_length`

The actual logic is in the sync and validation steps:

- [api/app/datasources.py](../api/app/datasources.py)
- [api/app/metadata.py](../api/app/metadata.py)

## Template and schema sources

The file below is a **template**, not a ready-to-upload metadata document. Replace each `<...>` placeholder with a value of the indicated type. `annotations` is intentionally empty because AeroLake generates annotations with its toolkit.

- [iqengine-metadata-schema-example.json](./iqengine-metadata-schema-example.json)
- [iqengine-metadata-real-example.json](./iqengine-metadata-real-example.json): compact snapshot of one document currently stored in MongoDB.

The real snapshot was read from the `IQEngine.metadata` collection in the local MongoDB container. It includes the complete `global` object and the first stored item from `captures` and `annotations`; the live document contains additional annotations. At the time it was inspected, the collection contained 3 records.

The template is based on three separate sources of truth:

1. The local SigMF JSON schema in [client/src/data/sigmf-schema.json](../client/src/data/sigmf-schema.json). This defines the standard `global`, `captures`, and `annotations` structure, required fields, property types, defaults, and datatype pattern.
2. IQEngine backend validation in [api/app/metadata.py](../api/app/metadata.py#L11-L24). This currently requires `global` and `global.core:datatype`, defaults `core:sample_rate` to `1`, creates a minimal `captures` entry when captures is missing or empty, and normalizes dictionary-valued `core:extensions` to an empty list.
3. IQEngine datasource synchronization in [api/app/datasources.py](../api/app/datasources.py#L169-L183). This adds `traceability:origin`, `traceability:revision`, and `traceability:sample_length` while indexing a matching `.sigmf-meta` and `.sigmf-data` pair.

The backend validator is less strict than the client SigMF schema. For interoperability, produce documents that satisfy the local SigMF schema as well as the backend validator.

The template has this general structure:

See the linked JSON template for field-by-field placeholders and possible value types.

## Notes on schema flexibility

IQEngine does not require a single rigid metadata schema for every recording.

The validation logic is intentionally permissive:

- `global.core:datatype` and `global.core:version` are required by the local client SigMF schema. The backend validator explicitly checks `core:datatype` but does not currently check `core:version`.
- `global.core:sample_rate` is defaulted to `1` if missing
- `captures` and `annotations` are required top-level properties in the local client schema. The backend creates a minimal `captures` entry if captures is absent or empty; annotations are not populated by IQEngine.
- `core:extensions` is normalized to an empty list if provided as a dict

The practical result is:

- different recordings may have different optional SigMF fields
- `captures` and `annotations` can vary by file
- the same metadata document does not have to look identical across all recordings

This is expected for SigMF-based catalogs. Standard optional fields such as `core:trailing_bytes` are supported by the local SigMF schema, but they should only be included when they describe the recording.

### Field ownership

| Field or group | Source | Required? | Guidance |
| --- | --- | --- | --- |
| `global`, `captures`, `annotations` | Local SigMF schema | Yes for client interoperability | Keep all three top-level properties. `annotations` may be `[]`. |
| `global.core:datatype` | SigMF schema and backend validator | Yes | Must match the stored dataset format. |
| `global.core:version` | Local SigMF schema | Yes for client interoperability | Use the SigMF version used to create the metadata. |
| Other `core:*` fields | Local SigMF schema | Usually optional | Include only when known; see the template and local schema. |
| `global.traceability:origin` | IQEngine sync code | Added by IQEngine sync | Identifies the source account, container, and object path. Do not treat it as a standard SigMF field. |
| `global.traceability:revision` | IQEngine sync code | Added by IQEngine sync | Catalog bookkeeping; confirm ownership before client-side writes. |
| `global.traceability:sample_length` | IQEngine sync code | Added by IQEngine sync | Calculated from `.sigmf-data` size and datatype. |
| `global.iqengine:geotrack` | No schema/source found | No | It is absent from the real snapshot and removed from the template. Add it only as a separately agreed extension, with an extension definition and owner. |
| `global.aerolake:signal_type` | Present in the real AeroLake record | No | Custom AeroLake metadata, not part of the local SigMF schema or IQEngine validator. |
| `annotations` entries | AeroLake toolkit | No IQEngine-generated values | Leave empty in this template; populate through the AeroLake annotation workflow. |

### Signal type

`global.aerolake:signal_type` is a custom AeroLake field observed in the real MongoDB record. It is useful for an application-level category such as `iridium`, `ais`, or `ads-b`, but it is not defined by the local SigMF schema and IQEngine does not validate or populate it. Keep it only if AeroLake owns and documents this extension.

### Geolocation and geotrack

`global.core:geolocation` is a standard optional SigMF global field for the recording location. IQEngine also supports geolocation on capture and annotation segments through `captures.core:geolocation` and `annotations.core:geolocation`. The API uses these fields for radius searches with MongoDB `$near` queries. A location can be supplied as a point or another GeoJSON geometry, depending on the query path.

`global.iqengine:geotrack` is different. The repository has a `/track` endpoint that reads this optional key and returns it if present, but the local SigMF schema does not define it, the sync process does not create it, and the real record inspected did not contain it. It should not be included in the standard template. If AeroLake needs a path or moving-platform track, define it as an explicit custom extension with an owner, format, and version.

### Trailing bytes

`global.core:trailing_bytes` is a standard optional SigMF field. It specifies how many bytes at the end of a non-conforming dataset file must be ignored because they are not IQ sample data. It is relevant only when the dataset contains extra terminal bytes; otherwise omit it. It is not the same as `captures.core:header_bytes`, which describes non-sample bytes preceding a capture chunk.

## Search and query fields

The query logic in [api/app/metadata.py](../api/app/metadata.py#L126-L265) builds MongoDB filters from the following fields. These are fields IQEngine can query; the repository does not currently declare MongoDB `create_index` or `ensure_index` calls for them. Production deployments should verify MongoDB query plans and add database indexes separately if the dataset size requires them.

| Query/filter field | Purpose | Example use |
| --- | --- | --- |
| `global.traceability:origin.account` | Filter by source account | account lookup |
| `global.traceability:origin.container` | Filter by source container | bucket or dataset |
| `global.traceability:origin.file_path` | Direct record identity | exact file path |
| `captures.core:frequency` | Frequency range filter | min/max frequency |
| `global.core:author` | Search by author | metadata author |
| `global.core:description` | Text search in description | free-text description |
| `annotations.core:label` | Search labels in annotations | semantic tags |
| `annotations.core:description` | Search annotation comments | notes or annotations |
| `captures.core:datetime` | Date/time range filter | time window |
| `captures.core:geolocation` | Geo filtering on captures | radius search |
| `annotations.core:geolocation` | Geo filtering on annotations | radius search |
| `global.traceability:origin.type` | Source type metadata | internal/external origin |
| `global.aerolake:modified` | Metadata modified-time range | incremental refresh |
| `global.aerolake:signal_type` | Signal category | iridium, ais, ads-b |
| `global.core:hw` | Hardware description | bladerf |
| `global.core:geolocation` | Global recording location | radius search |
| `global.aerolake:operator` | Recording operator | operator lookup |
| `global.core:recorder` | Recorder software | GR-ION |

The effective search surface is:

- account and container filters
- frequency windows
- author/description text search
- annotation label/comment search
- time window filters
- geolocation radius filters
- free text search across metadata/annotations

### Query examples supported by the API

The metadata query helper supports these inputs:

- `account`
- `container`
- `database_id`
- `min_frequency`
- `max_frequency`
- `author`
- `description`
- `label`
- `comment`
- `captures_geo`
- `annotations_geo`
- `min_datetime`
- `max_datetime`
- `text`
- `min_modified` / `max_modified`
- `signal_type`
- `hw`
- `location` (longitude, latitude, radius)
- `operator`
- `recorder`

This is the primary catalog search contract exposed by the code.

## External datasource sync behavior

When an external datasource (for example MinIO or Azure Blob) is synced, IQEngine does the following:

1. lists candidate files under the configured source
2. filters for `.sigmf-meta` and matching `.sigmf-data`
3. parses the metadata JSON
4. validates the metadata with `validate_metadata()`
5. adds traceability fields under `global.traceability:origin`
6. calculates sample length from the data payload length and datatype
7. upserts the final document into the MongoDB metadata collection

The source code for this is in [api/app/datasources.py](../api/app/datasources.py#L69-L224).

### AeroLake requirements

AeroLake must write these fields into each `.sigmf-meta` document before IQEngine syncs it:

- `global.aerolake:modified`: ISO-8601 timestamp representing the source metadata's last-modified time.
- `global.aerolake:signal_type`: the controlled signal category.
- `global.aerolake:operator`: the recording operator.
- `global.core:hw`, `global.core:geolocation`, and `global.core:recorder`: standard SigMF fields.

IQEngine does not derive modified time or operator from storage metadata. The AeroLake metadata producer or its storage-to-SigMF sync job owns population and update semantics. `location` queries use `global.core:geolocation` and require the same GeoJSON/2dsphere MongoDB setup as other `$near` queries.

## Operational guidance for AeroLake

For an AeroLake integration, the safe pattern is:

- keep MinIO or the upstream storage system as the source of truth
- let IQEngine act as the derived catalog and query layer
- do not write directly to MongoDB as the primary source of records
- use IQEngine APIs to fetch catalog entries after a sync or refresh

This is the recommended operational model:

- raw data files live in external storage
- `.sigmf-meta` and `.sigmf-data` are created or updated in that storage
- the IQEngine sync step indexes the new/changed objects
- catalog queries reference the indexed metadata
- deletions should be treated as a reconciliation problem, not as an assumed automatic cleanup unless explicitly implemented

## Production-readiness notes

The code shows the main production concerns clearly:

- the catalog is derived, not authoritative
- deletion handling is not a built-in “hard sync” guarantee unless the sync job explicitly removes stale records
- ownership of metadata is tied to the external source path and account/container metadata
- service-to-service authentication should be handled at the API layer, not by coupling directly to MongoDB internals

For AeroLake, that means the integration contract should specify:

- which storage bucket/account is authoritative
- how sync is triggered
- how often refresh/reconciliation runs
- how delete events are handled
- which service identity is allowed to query or sync
- what metadata ownership rules apply when files are moved or replaced

## API and path conventions

The route layer exposes the external-facing access points in [api/app/datasources_router.py](../api/app/datasources_router.py).

Core catalog access patterns include:

- `POST /api/datasources`
- `PUT /api/datasources/{account}/{container}/sync`
- `GET /api/datasources/query`
- `GET /api/datasources/{account}/{container}/{filepath:path}/meta`
- `POST /api/datasources/{account}/{container}/{filepath:path}/meta`

In practice, the catalog API is intended to be used as the integration boundary, while raw storage remains the true data source.

## Source-of-truth principle

The important architectural rule is:

> MinIO/Azure Blob is the source of truth for recordings; IQEngine is the searchable metadata catalog built from those files.

That is the correct pattern for AeroLake if the goal is to reuse IQEngine’s indexing and search without making MongoDB the canonical storage layer.

## Related files

- [iqengine-metadata-schema-example.json](./iqengine-metadata-schema-example.json)
- [api/app/datasources.py](../api/app/datasources.py)
- [api/app/metadata.py](../api/app/metadata.py)
- [api/app/datasources_router.py](../api/app/datasources_router.py)
- [api/tests/test_data.py](../api/tests/test_data.py)
