# Analytics platform and agent — editable diagrams

Open **[analytics-platform-and-agent.drawio](analytics-platform-and-agent.drawio)** in draw.io
Desktop or diagrams.net. All seven tabs use editable shapes and connectors, with vendor icons on the cloud hosting views.

| Tab | Scope | Preview |
| --- | --- | --- |
| 01 Platform | Postgres → dlt → ClickHouse bronze/staging/marts; Dagster, dbt, Cube, Superset, agent | [PNG](platform.png) |
| 02 Agent analysis | Access gate, metadata discovery, SQL, verification/retry, answer | [PNG](agent-analysis.png) |
| 03 Charts and persistence | Standard and custom charts, execute approval, object storage, frontend reload | [PNG](chart-persistence.png) |
| 04 GCP deployment (proposed) | Cloud SQL, GKE, ClickHouse and Cloud Storage with vendor icons | [PNG](gcp-proposal.png) |
| 05 AWS deployment (proposed) | RDS PostgreSQL, EKS, ClickHouse and S3 | [PNG](aws-proposal.png) |
| 06 Azure deployment (proposed) | PostgreSQL Flexible Server, AKS, ClickHouse and Blob Storage | [PNG](azure-proposal.png) |
| 07 Oracle Cloud deployment (proposed) | OCI PostgreSQL candidate, OKE, Block Volume and Object Storage | [PNG](oci-proposal.png) |

Tabs 01–03 describe the repository implementation reviewed on 2026-10-04, not a live deployment
audit. Tabs 04–07 propose comparable GCP, AWS, Azure and Oracle Cloud hosting arrangements; they are not
implemented or deployed. The analysis tab illustrates the loop prescribed by the agent's skills;
the model chooses tool
calls dynamically. It is not a fixed LangGraph node sequence.

The `.drawio` file is the editable visual source. The seven `.ir.json` files retain semantic models,
source provenance and initial layout coordinates. Shape properties in draw.io also carry source
paths. Use incremental sync for future source changes and retain the curated layout; a fresh CLI
build from IR may replace the layout. PNG files are viewing previews, without embedded diagram XML.

## Key boundaries

- Cube supplies metric definitions to the agent. The agent executes SQL directly against ClickHouse.
- SQL access covers `nextrole_marts` and `nextrole_staging`; bronze is denied. Server constraints cap
  execution at 30 seconds and results at 10,000 rows, with additional resource limits.
- Multi-user access requires an explicit ID/email allowlist. Queries span users; per-caller row
  policies are future work. Saved chart artifacts use caller-scoped storage.
- Extraction removes document/message bodies and sensitive payloads. Some tagged PII remains,
  including display names, email domains and IPv4 prefixes.
- Message timestamps represent first capture. Cost is a list-price estimate with incomplete token
  coverage. Hourly scheduling does not guarantee freshness after failed runs.
- Standard charts execute their SQL inside `create_chart`. Custom Python figures use scratch files
  and the shared execute-approval policy before entering the same storage path.

## Source map

Paths below are relative to the repository root; individual diagram components embed their sources.

| Area | Evidence |
| --- | --- |
| Topology and services | `docker-compose.yml`, `analytics/README.md` |
| Extraction privacy | `analytics/nextrole_analytics/dlt_sources/transforms.py` |
| Orchestration | `analytics/nextrole_analytics/schedules.py`, `assets_dbt.py` |
| Tables and metrics | `analytics/dbt/models/`, `analytics/cube/model/` |
| Dashboard | `analytics/superset/assets/dashboard_export/` |
| Agent and access | `backend/agents/analytics_agent/agents.py`, `access.py`, `middleware.py` |
| Dictionary and tools | `backend/agents/analytics_agent/metadata.py`, `tools.py` |
| Analysis guidance | `backend/agents/analytics_agent/ANALYTICS_AGENT.md`, `skills/analytics-agent/warehouse-analysis/SKILL.md` |
| Warehouse constraints | `analytics/clickhouse/users.d/analytics-agent.xml` |
| Charts | `backend/agents/analytics_agent/charts.py`, `skills/analytics-agent/charts/SKILL.md` |
| Frontend | `frontend/src/app/components/TopBar.tsx`, `frontend/src/app/lib/charts.ts`, `frontend/src/app/components/charts/InlineChart.tsx` |

The Dagster/dbt asset-check convention was also cross-checked against
[Dagster's dbt reference](https://github.com/dagster-io/dagster/blob/master/docs/docs/integrations/libraries/dbt/reference.md).

## Validation and preview export

From the repository root:

```sh
python3 .agents/skills/drawio-skill/scripts/validate.py \
  docs/diagrams/analytics-drawio/analytics-platform-and-agent.drawio --score

/Applications/draw.io.app/Contents/MacOS/draw.io -x -f png --width 2000 \
  --border 30 --page-index 1 \
  -o docs/diagrams/analytics-drawio/platform.png \
  docs/diagrams/analytics-drawio/analytics-platform-and-agent.drawio
```

Page indexes are 1-based: 2 is agent analysis; 3 is chart persistence; 4 is GCP; 5 is AWS; 6 is Azure; 7 is OCI.
The exports were inspected
visually. Semantic connectivity checks apply to the IR models, excluding page titles and notes;
they validate the authored diagram, not runtime behavior.

## Proposed Google Cloud hosting

The fourth tab uses official Google Cloud shapes and an embedded ClickHouse logo. GKE icons identify
hosting; the labels identify the application running there. All icon assets are embedded, so viewing
and exporting the diagram does not require fetching them from a CDN.

- [Cloud SQL for PostgreSQL](https://docs.cloud.google.com/sql/docs/postgres/introduction) hosts the
  operational PostgreSQL database. Separate Dagster/Superset metadata databases also need provisioning.
- [GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/kubernetes-engine-overview)
  hosts Dagster, dlt/dbt, dbt docs, ClickHouse, Cube, Superset and the analytics backend. ClickHouse
  needs persistent storage; ingress, IAM, secrets, backup and availability design remain deployment work.
- [Cloud Storage interoperability](https://docs.cloud.google.com/storage/docs/interoperability)
  provides a candidate artifact store. The current code uses `obstore.store.S3Store`, so configure
  the Cloud Storage XML endpoint and HMAC credentials and validate supported operations. This is not
  a claim that a GCS deployment has already been tested.
- ClickHouse remains the SQL engine. BigQuery is not a drop-in change for the ClickHouse client,
  dialect, dbt adapter and warehouse access controls in this repository.

The view focuses on analytics hosting, not the complete application deployment. Existing app runtime
services, including Redis, and detailed network/security topology are outside this tab's scope.
Logo attribution: draw.io Google Cloud shape library; ClickHouse via Simple Icons (CC0), retrieved
with the drawio skill icon resolver. Trademarks identify their respective products.

## Equivalent AWS and Azure proposals

Tabs 05 and 06 reuse the GCP layout so the cloud-service substitutions are easy to compare.
ClickHouse, Dagster, dlt/dbt, Cube, Superset and the analytics agent retain their application roles.
The cloud Kubernetes icons indicate the hosting platform, not vendor ownership of these tools.

| Role | GCP | AWS | Azure |
| --- | --- | --- | --- |
| Operational PostgreSQL | Cloud SQL | RDS for PostgreSQL | Azure Database for PostgreSQL Flexible Server |
| Container hosting | GKE | EKS with EC2 worker nodes | AKS |
| ClickHouse volumes | Persistent disk | EBS via CSI | Azure Disk via CSI |
| Chart artifacts | Cloud Storage | S3 | Blob Storage |
| Current client fit | XML API/HMAC interoperability needs validation | Existing S3Store client; configure and validate | Add AzureStore/provider selection; not an endpoint-only change |

AWS source references:

- [RDS for PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html)
- [EKS](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [EBS CSI storage](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html): the proposed
  stateful ClickHouse workload uses EC2-backed nodes. EBS volumes cannot be mounted to Fargate pods.
- [S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html): the existing
  `build_store_from_settings` constructs `S3Store`. Configure and test the bucket, region, endpoint,
  credentials and access policies. This diagram does not claim workload identity is already wired.

Azure source references:

- [Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/overview)
- [AKS](https://learn.microsoft.com/en-us/azure/aks/what-is-aks)
- [Azure Disk CSI storage](https://learn.microsoft.com/en-us/azure/aks/azure-csi-disk-storage-provision)
- [Blob Storage](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blobs-introduction):
  the repository's object client currently constructs `S3Store`, so native Azure storage requires
  a provider-selection and authentication change plus tests for file operations and user scoping.
  The proposed adapter is [obstore.store.AzureStore](https://developmentseed.org/obstore/latest/api/store/azure/);
  no backend code was changed in this task.

Each proposal still needs infrastructure provisioning, private networking, ingress, secret management,
separate metadata databases, storage configuration, backup/recovery and availability design. These
views omit the rest of the application runtime (including Redis) just as the GCP tab does.

AWS/Azure icons use the official draw.io shape libraries. Azure SVG icons are embedded in the file;
AWS and Blob Storage stencil shapes render natively in draw.io. No CDN access is required to open
or export the diagrams. Existing tabs and their layouts are preserved.

## Oracle Cloud proposal

Tab 07 uses the same application roles and layout, with icons sourced from Oracle's official
[OCI Architecture Diagram Toolkit](https://docs.oracle.com/en-us/iaas/Content/General/Reference/graphicsfordiagrams.htm).
The database uses the toolkit's generic Database category icon, explicitly labeled PostgreSQL.
The toolkit's OKE and Object Storage icons are embedded as SVG images for portable rendering;
service cards, labels and connectors remain editable.

| Role | Proposed OCI service |
| --- | --- |
| Operational PostgreSQL | OCI Database with PostgreSQL, subject to version compatibility |
| Analytics containers | OCI Kubernetes Engine (OKE) |
| ClickHouse persistence | OCI Block Volume via CSI persistent volumes |
| Chart artifacts | OCI Object Storage through its Amazon S3 Compatibility API |

- [OCI Database with PostgreSQL](https://docs.oracle.com/en-us/iaas/Content/postgresql/overview.htm)
  is the managed candidate. The repository pins `pgvector/pgvector:pg18`, while Oracle's
  [major-version guide](https://docs.oracle.com/en-us/iaas/Content/postgresql/upgrades.htm) currently
  lists PostgreSQL 14–17. A direct migration of the existing PG18 database is not established.
  Recheck service support in the target region and validate application/data compatibility; if PG18
  must be retained, a self-managed deployment is an alternative requiring its own operations design.
  Oracle's [extension list](https://docs.oracle.com/en-us/iaas/Content/postgresql/extensions.htm)
  includes pgvector but requires enabling it in configuration.
- [OKE Block Volume provisioning](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcreatingpersistentvolumeclaim_topic-Provisioning_PVCs_on_BV.htm)
  supplies durable volumes for ClickHouse. Configure CSI, storage classes, backups and recovery;
  the diagram does not establish replication or high availability.
- [Object Storage S3 compatibility](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi_topic-Amazon_S3_Compatibility_API_Support.htm)
  is a candidate for the existing `S3Store` client. Configure the tenancy namespace, regional
  compatibility endpoint, bucket, signing region and
  [customer secret keys](https://docs.oracle.com/en-us/iaas/Content/Identity/access/working-with-customer-secret-keys.htm).
  Verify addressing and supported read/write/list/delete operations plus user scoping; this task
  did not run an OCI integration test.

The same provisioning work and omitted application dependencies described for the other cloud tabs
apply here. This is a proposed hosting view; no Oracle Cloud resources or backend changes were made.
Oracle icon assets are copyright Oracle and/or its affiliates and are used for architecture diagrams
under the published toolkit guidance.
