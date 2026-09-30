CREATE TABLE users (
    id uuid PRIMARY KEY,
    display_name text NOT NULL,
    auth_provider text,
    auth_subject text,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(auth_provider, auth_subject),
    CHECK ((auth_provider IS NULL) = (auth_subject IS NULL))
);
CREATE TABLE user_roles (
    user_id uuid REFERENCES users(id),
    role text CHECK(role IN ('player','coach','admin')),
    PRIMARY KEY(user_id,role)
);
CREATE TABLE coach_assignments (
    coach_id uuid REFERENCES users(id), player_id uuid REFERENCES users(id),
    PRIMARY KEY(coach_id,player_id)
);
CREATE TABLE review_streams (
    user_id uuid REFERENCES users(id), id text, data jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY(user_id,id)
);
CREATE TABLE matches (
    id text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), stream text NOT NULL,
    started double precision NOT NULL, ended double precision, updated double precision NOT NULL,
    game text NOT NULL CHECK(game IN ('hok','lol')), hero text,
    outcome text NOT NULL DEFAULT 'unknown' CHECK(outcome IN ('win','loss','unknown')),
    status text NOT NULL DEFAULT 'recording', samples integer NOT NULL DEFAULT 0,
    token text, UNIQUE(id,user_id),
    FOREIGN KEY(user_id,stream) REFERENCES review_streams(user_id,id)
);
CREATE INDEX matches_owner_time ON matches(user_id,started DESC);
CREATE TABLE match_reviews (
    match_id text PRIMARY KEY, user_id uuid NOT NULL, version integer NOT NULL DEFAULT 1,
    result jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(match_id,user_id) REFERENCES matches(id,user_id)
);
CREATE TABLE match_assets (
    id text PRIMARY KEY, user_id uuid NOT NULL, match_id text NOT NULL,
    storage_key text NOT NULL, mime_type text NOT NULL DEFAULT 'image/jpeg',
    UNIQUE(id,user_id), FOREIGN KEY(match_id,user_id) REFERENCES matches(id,user_id)
);
CREATE TABLE match_events (
    id text PRIMARY KEY, user_id uuid NOT NULL, match_id text NOT NULL,
    captured double precision NOT NULL, observation jsonb NOT NULL, priority integer NOT NULL,
    FOREIGN KEY(id,user_id) REFERENCES match_assets(id,user_id),
    FOREIGN KEY(match_id,user_id) REFERENCES matches(id,user_id)
);
CREATE INDEX match_events_owner_match ON match_events(user_id,match_id,captured);
CREATE TABLE chat_sessions (
    id text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id),
    revision integer NOT NULL DEFAULT 0, context jsonb NOT NULL DEFAULT '{}',
    timers jsonb NOT NULL DEFAULT '[]', updated double precision NOT NULL,
    busy_until double precision NOT NULL DEFAULT 0, token text,
    UNIQUE(id,user_id)
);
CREATE INDEX chat_sessions_owner ON chat_sessions(user_id,updated DESC);
CREATE TABLE chat_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id uuid NOT NULL,
    session_id text NOT NULL, request_id text NOT NULL,
    role text NOT NULL CHECK(role IN ('user','assistant')), content text NOT NULL,
    response jsonb, created_at timestamptz NOT NULL DEFAULT now(),
    match_id text, UNIQUE(session_id,request_id,role), UNIQUE(id,user_id),
    FOREIGN KEY(session_id,user_id) REFERENCES chat_sessions(id,user_id),
    FOREIGN KEY(match_id,user_id) REFERENCES matches(id,user_id)
);
CREATE INDEX chat_messages_owner_time ON chat_messages(user_id,id);
CREATE TABLE player_profiles (
    user_id uuid PRIMARY KEY REFERENCES users(id), version integer NOT NULL DEFAULT 0,
    data jsonb NOT NULL DEFAULT '{"facts":[]}', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE profile_update_jobs (
    id text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), match_id text NOT NULL,
    review_version integer NOT NULL, status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0, token text, lease_until timestamptz,
    input jsonb, progress integer NOT NULL DEFAULT 0, changes jsonb NOT NULL DEFAULT '[]',
    model text, error text, available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id,match_id,review_version), UNIQUE(id,user_id),
    FOREIGN KEY(match_id,user_id) REFERENCES matches(id,user_id)
);
CREATE INDEX profile_jobs_pending ON profile_update_jobs(status,available_at,created_at);
CREATE TABLE profile_versions (
    user_id uuid NOT NULL REFERENCES users(id), version integer NOT NULL,
    job_id text NOT NULL UNIQUE, match_id text NOT NULL, data jsonb NOT NULL,
    changes jsonb NOT NULL, model text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,version), FOREIGN KEY(job_id,user_id) REFERENCES profile_update_jobs(id,user_id),
    FOREIGN KEY(match_id,user_id) REFERENCES matches(id,user_id)
);
CREATE TABLE profile_consumed_messages (
    message_id bigint PRIMARY KEY, user_id uuid NOT NULL, job_id text NOT NULL,
    FOREIGN KEY(message_id,user_id) REFERENCES chat_messages(id,user_id),
    FOREIGN KEY(job_id,user_id) REFERENCES profile_update_jobs(id,user_id)
);
CREATE TABLE profile_facts (
    user_id uuid NOT NULL REFERENCES users(id), scope text NOT NULL CHECK(scope IN ('global','hok','lol')),
    key text NOT NULL, data jsonb NOT NULL, updated_version integer NOT NULL,
    PRIMARY KEY(user_id,scope,key), FOREIGN KEY(user_id,updated_version) REFERENCES profile_versions(user_id,version)
);
CREATE TABLE profile_evidence (
    user_id uuid NOT NULL, scope text NOT NULL, key text NOT NULL,
    source_id text NOT NULL, job_id text NOT NULL, content jsonb NOT NULL,
    PRIMARY KEY(user_id,scope,key,source_id),
    FOREIGN KEY(user_id,scope,key) REFERENCES profile_facts(user_id,scope,key),
    FOREIGN KEY(job_id,user_id) REFERENCES profile_update_jobs(id,user_id)
);
CREATE TABLE access_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id uuid NOT NULL REFERENCES users(id), target_id uuid NOT NULL REFERENCES users(id),
    action text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
