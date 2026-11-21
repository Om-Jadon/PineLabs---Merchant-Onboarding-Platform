CREATE SCHEMA sim;

CREATE TABLE cases (
	id VARCHAR(36) NOT NULL,
	owner VARCHAR NOT NULL,
	submission_key VARCHAR NOT NULL,
	payload_hash VARCHAR NOT NULL,
	legal_name VARCHAR NOT NULL,
	pan VARCHAR NOT NULL,
	gstin VARCHAR NOT NULL,
	policy JSON NOT NULL,
	scenario VARCHAR NOT NULL,
	state VARCHAR NOT NULL,
	reason VARCHAR NOT NULL,
	requested_kind VARCHAR,
	request_note VARCHAR,
	issuance VARCHAR NOT NULL,
	mid VARCHAR,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	state_since TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner, submission_key),
	UNIQUE (mid)
);

CREATE INDEX ix_cases_state ON cases (state);

CREATE TABLE sim.deliveries (
	attempt_id VARCHAR NOT NULL,
	provider VARCHAR NOT NULL,
	payload JSON NOT NULL,
	due TIMESTAMP WITH TIME ZONE NOT NULL,
	delivered BOOLEAN NOT NULL,
	tries INTEGER NOT NULL,
	PRIMARY KEY (attempt_id)
);

CREATE INDEX ix_sim_deliveries_due ON sim.deliveries (due);

CREATE TABLE sim.merchants (
	case_id VARCHAR NOT NULL,
	mid VARCHAR NOT NULL,
	PRIMARY KEY (case_id),
	UNIQUE (mid)
);

CREATE TABLE attempts (
	id VARCHAR(36) NOT NULL,
	case_id VARCHAR(36) NOT NULL,
	kind VARCHAR NOT NULL,
	number INTEGER NOT NULL,
	retry INTEGER NOT NULL,
	fingerprint VARCHAR NOT NULL,
	is_current BOOLEAN NOT NULL,
	status VARCHAR NOT NULL,
	reason VARCHAR NOT NULL,
	provider_ref VARCHAR,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	deadline TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (case_id, kind, number),
	FOREIGN KEY(case_id) REFERENCES cases (id)
);

CREATE INDEX ix_attempts_case_id ON attempts (case_id);

CREATE UNIQUE INDEX one_current_attempt ON attempts (case_id, kind) WHERE is_current;

CREATE TABLE documents (
	id VARCHAR(36) NOT NULL,
	case_id VARCHAR(36) NOT NULL,
	kind VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	filename VARCHAR NOT NULL,
	mime VARCHAR NOT NULL,
	data BYTEA NOT NULL,
	method VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	detected_kind VARCHAR,
	reader_note VARCHAR,
	fields JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (case_id, kind, revision),
	FOREIGN KEY(case_id) REFERENCES cases (id)
);

CREATE INDEX ix_documents_case_id ON documents (case_id);

CREATE TABLE events (
	id SERIAL NOT NULL,
	case_id VARCHAR(36) NOT NULL,
	at TIMESTAMP WITH TIME ZONE NOT NULL,
	actor VARCHAR NOT NULL,
	title VARCHAR NOT NULL,
	detail VARCHAR NOT NULL,
	source_id VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(case_id) REFERENCES cases (id)
);

CREATE INDEX ix_events_case_id ON events (case_id);

CREATE TABLE jobs (
	id VARCHAR(36) NOT NULL,
	case_id VARCHAR(36) NOT NULL,
	kind VARCHAR NOT NULL,
	source VARCHAR NOT NULL,
	operation_key VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	at TIMESTAMP WITH TIME ZONE NOT NULL,
	tries INTEGER NOT NULL,
	lease_token VARCHAR,
	lease_until TIMESTAMP WITH TIME ZONE,
	error VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(case_id) REFERENCES cases (id),
	UNIQUE (operation_key)
);

CREATE INDEX ix_jobs_at ON jobs (at);

CREATE INDEX ix_jobs_case_id ON jobs (case_id);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE TABLE receipts (
	provider VARCHAR NOT NULL,
	event_id VARCHAR NOT NULL,
	attempt_id VARCHAR(36) NOT NULL,
	case_id VARCHAR(36) NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE NOT NULL,
	disposition VARCHAR NOT NULL,
	digest VARCHAR NOT NULL,
	deliveries INTEGER NOT NULL,
	PRIMARY KEY (provider, event_id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id),
	FOREIGN KEY(case_id) REFERENCES cases (id)
);

CREATE INDEX ix_receipts_case_id ON receipts (case_id);
