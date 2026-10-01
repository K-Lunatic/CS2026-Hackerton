-- SQLite-compatible shared schema draft.
-- TLS-owned tables are source data; app-owned tables are collaboration data.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  external_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  department TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
  id TEXT PRIMARY KEY,
  external_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  professor TEXT,
  semester TEXT,
  source TEXT NOT NULL DEFAULT 'tls',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollments (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'STUDENT',
  PRIMARY KEY (user_id, course_id)
);

CREATE TABLE IF NOT EXISTS assignments (
  id TEXT PRIMARY KEY,
  external_id TEXT NOT NULL,
  course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT,
  due_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'tls',
  updated_at TEXT NOT NULL,
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS assignment_submissions (
  assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  submission_status TEXT NOT NULL CHECK (submission_status IN ('NOT_SUBMITTED', 'SUBMITTED', 'LATE', 'UNKNOWN')),
  submitted_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (assignment_id, user_id)
);

CREATE TABLE IF NOT EXISTS lectures (
  id TEXT PRIMARY KEY,
  external_id TEXT NOT NULL,
  course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 0),
  source TEXT NOT NULL DEFAULT 'tls',
  updated_at TEXT NOT NULL,
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS lecture_progress (
  lecture_id TEXT NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  watched_seconds INTEGER NOT NULL DEFAULT 0 CHECK (watched_seconds >= 0),
  watch_progress REAL NOT NULL DEFAULT 0 CHECK (watch_progress >= 0 AND watch_progress <= 100),
  completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (lecture_id, user_id)
);

CREATE TABLE IF NOT EXISTS notices (
  id TEXT PRIMARY KEY,
  external_id TEXT NOT NULL,
  course_id TEXT REFERENCES courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  published_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'tls',
  updated_at TEXT NOT NULL,
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS bookmarks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type TEXT NOT NULL CHECK (target_type IN ('ASSIGNMENT', 'LECTURE', 'COURSE', 'NOTICE', 'PROJECT', 'CUSTOM')),
  target_id TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'MEMBER',
  PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT,
  assignee_id TEXT REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'TODO' CHECK (status IN ('TODO', 'IN_PROGRESS', 'DONE')),
  due_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handovers (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  author_id TEXT NOT NULL REFERENCES users(id),
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handover_items (
  id TEXT PRIMARY KEY,
  handover_id TEXT NOT NULL REFERENCES handovers(id) ON DELETE CASCADE,
  item_type TEXT NOT NULL CHECK (item_type IN ('COMPLETED', 'PENDING', 'FILE', 'ENVIRONMENT', 'NEXT_ACTION')),
  content TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_course_due ON assignments(course_id, due_at);
CREATE INDEX IF NOT EXISTS idx_submissions_user_status ON assignment_submissions(user_id, submission_status);
CREATE INDEX IF NOT EXISTS idx_progress_user_completed ON lecture_progress(user_id, completed);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON project_tasks(project_id, status);
