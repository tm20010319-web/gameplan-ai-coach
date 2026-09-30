CREATE TABLE timer_sessions (
    user_id uuid NOT NULL REFERENCES users(id), id text NOT NULL,
    memory jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,id)
);
CREATE TABLE timer_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL, session_id text NOT NULL, data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(user_id,session_id) REFERENCES timer_sessions(user_id,id)
);
