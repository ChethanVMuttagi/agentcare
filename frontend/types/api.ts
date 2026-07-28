/**
 * TypeScript mirrors of every backend Pydantic schema this frontend
 * consumes. Kept as ONE file, deliberately: these types describe a
 * single, already-fixed wire contract (the FastAPI backend, feature
 * complete through STORY-015) — not something this frontend evolves
 * independently. If the backend's schema changes, this file changes to
 * match it; it never leads.
 *
 * Field names/optionality match the Pydantic models exactly (see the
 * backend's `app/schemas/*.py`). Enums are string literal unions, not
 * TS `enum`, so a value coming over the wire is trivially assignable
 * without a runtime mapping step.
 */

// ---------------------------------------------------------------------------
// Shared / errors
// ---------------------------------------------------------------------------

export interface ErrorDetail {
  code: string;
  message: string;
}

export interface ErrorResponse {
  error: ErrorDetail;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface TokenRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
}

export interface CurrentUserResponse {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Membership / roles (app/models/membership.py) — not its own API resource,
// but every route's authorization is expressed in these terms, and the
// frontend's own role-based UI hints (see lib/session.ts) use this type.
// ---------------------------------------------------------------------------

export type Role = "admin" | "staff" | "patient" | "supervisor";

// ---------------------------------------------------------------------------
// Patients
// ---------------------------------------------------------------------------

export interface PatientCreate {
  patient_number: string;
  first_name: string;
  last_name: string;
  date_of_birth: string;
  user_id?: string | null;
}

export interface PatientResponse {
  id: string;
  organization_id: string;
  user_id: string | null;
  patient_number: string;
  first_name: string;
  last_name: string;
  date_of_birth: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PatientListResponse {
  patients: PatientResponse[];
}

// ---------------------------------------------------------------------------
// Departments
// ---------------------------------------------------------------------------

export interface DepartmentCreate {
  facility_id: string;
  name: string;
  code: string;
}

export interface DepartmentResponse {
  id: string;
  organization_id: string;
  facility_id: string;
  name: string;
  code: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface DepartmentListResponse {
  departments: DepartmentResponse[];
}

// ---------------------------------------------------------------------------
// Practitioners / availability
// ---------------------------------------------------------------------------

export type PractitionerType =
  | "physician"
  | "physiotherapist"
  | "counselor"
  | "therapist"
  | "other";

export interface PractitionerCreate {
  first_name: string;
  last_name: string;
  practitioner_type: PractitionerType;
}

export interface PractitionerResponse {
  id: string;
  organization_id: string;
  first_name: string;
  last_name: string;
  practitioner_type: PractitionerType;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PractitionerListResponse {
  practitioners: PractitionerResponse[];
}

export interface PractitionerDepartmentResponse {
  id: string;
  organization_id: string;
  practitioner_id: string;
  department_id: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type DayOfWeek =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export interface AvailabilityCreate {
  department_id: string;
  day_of_week: DayOfWeek;
  start_time: string;
  end_time: string;
  timezone: string;
}

export interface AvailabilityResponse {
  id: string;
  organization_id: string;
  practitioner_id: string;
  department_id: string;
  day_of_week: DayOfWeek;
  start_time: string;
  end_time: string;
  timezone: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AvailabilityListResponse {
  availability: AvailabilityResponse[];
}

export interface AvailableTimeSlotResponse {
  start_at: string;
  end_at: string;
}

export interface AvailableTimesResponse {
  available_times: AvailableTimeSlotResponse[];
}

// ---------------------------------------------------------------------------
// Appointments
// ---------------------------------------------------------------------------

export type AppointmentStatus = "booked" | "cancelled" | "completed";

export interface AppointmentCreate {
  patient_id?: string | null;
  practitioner_id: string;
  department_id: string;
  start_at: string;
  duration_minutes: number;
}

export interface AppointmentRescheduleRequest {
  start_at: string;
  duration_minutes: number;
}

export interface AppointmentCancelRequest {
  cancellation_reason?: string | null;
}

export interface AppointmentResponse {
  id: string;
  organization_id: string;
  patient_id: string;
  practitioner_id: string;
  department_id: string;
  start_at: string;
  end_at: string;
  status: AppointmentStatus;
  cancellation_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface AppointmentListResponse {
  appointments: AppointmentResponse[];
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

export type DocumentType = "identity" | "insurance" | "referral" | "consent" | "other";
export type DocumentStatus = "pending" | "available" | "failed" | "deleted";
export type DocumentMediaType = "application/pdf" | "image/jpeg" | "image/png";

export interface DocumentResponse {
  id: string;
  organization_id: string;
  patient_id: string;
  uploaded_by_user_id: string;
  document_type: DocumentType;
  status: DocumentStatus;
  original_filename: string;
  media_type: DocumentMediaType;
  size_bytes: number | null;
  sha256: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  documents: DocumentResponse[];
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export type WorkflowRequestType =
  | "appointment_booking"
  | "appointment_rescheduling"
  | "appointment_cancellation"
  | "document_collection"
  | "administrative_routing"
  | "follow_up"
  | "reminder_delivery"
  | "patient_registration";

export type WorkflowStatus =
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled";

export type StepStatus = "pending" | "running" | "waiting" | "completed" | "failed" | "skipped";

export type ActorType = "user" | "system" | "agent" | "tool";

export type WorkflowEventType =
  | "workflow_created"
  | "workflow_started"
  | "step_started"
  | "tool_invoked"
  | "agent_handoff"
  | "reminder_scheduled"
  | "reminder_started"
  | "reminder_sent"
  | "reminder_failed"
  | "reminder_cancelled"
  | "step_completed"
  | "step_failed"
  | "step_skipped"
  | "step_waiting"
  | "step_resumed"
  | "approval_requested"
  | "approval_granted"
  | "approval_rejected"
  | "workflow_waiting"
  | "workflow_resumed"
  | "workflow_completed"
  | "workflow_failed"
  | "workflow_cancelled";

export interface WorkflowRunCreate {
  request_type: WorkflowRequestType;
  patient_id?: string | null;
  idempotency_key?: string | null;
}

export interface WorkflowRunResponse {
  id: string;
  organization_id: string;
  patient_id: string | null;
  initiated_by_user_id: string;
  request_type: WorkflowRequestType;
  status: WorkflowStatus;
  current_step: number | null;
  correlation_id: string;
  idempotency_key: string | null;
  failure_code: string | null;
  failure_message_safe: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRunListResponse {
  workflows: WorkflowRunResponse[];
}

export interface WorkflowStepResponse {
  id: string;
  organization_id: string;
  workflow_run_id: string;
  sequence_number: number;
  step_type: string;
  status: StepStatus;
  agent_name: string | null;
  tool_name: string | null;
  attempt_count: number;
  failure_code: string | null;
  failure_message_safe: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowStepListResponse {
  steps: WorkflowStepResponse[];
}

export interface WorkflowEventResponse {
  id: string;
  organization_id: string;
  workflow_run_id: string;
  workflow_step_id: string | null;
  event_type: WorkflowEventType;
  actor_type: ActorType;
  actor_identifier: string;
  safe_metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface WorkflowEventListResponse {
  events: WorkflowEventResponse[];
}

export interface WorkflowTimelineEntry {
  sequence: number;
  event_type: WorkflowEventType;
  actor_type: ActorType;
  actor_identifier: string;
  safe_metadata: Record<string, unknown> | null;
  created_at: string;
  workflow_step_id: string | null;
  step_sequence_number: number | null;
  step_type: string | null;
  step_agent_name: string | null;
}

export interface WorkflowTimelineResponse {
  workflow_id: string;
  status: WorkflowStatus;
  entries: WorkflowTimelineEntry[];
}

// ---------------------------------------------------------------------------
// Agent (AI Assistant)
// ---------------------------------------------------------------------------

export type DecisionKind =
  | "tool_call"
  | "clarification_required"
  | "safe_response"
  | "refusal"
  | "requires_approval";

export interface AgentExecuteRequest {
  request_type: WorkflowRequestType;
  request_text: string;
  patient_id?: string | null;
  workflow_run_id?: string | null;
}

export interface AgentExecuteResponse {
  workflow_id: string;
  workflow_status: WorkflowStatus;
  decision_kind: DecisionKind;
  handled_by_agent: string;
  message: string;
  tool_name: string | null;
  tool_result_code: string | null;
  tool_result_data: Record<string, unknown> | null;
  approval_id?: string | null;
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export type ApprovalType =
  | "appointment_override"
  | "manual_reschedule"
  | "document_exception"
  | "high_risk_action"
  | "custom";

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

export interface ApprovalRequestCreate {
  workflow_run_id: string;
  workflow_step_id: string;
  approval_type: ApprovalType;
  reason: string;
}

export interface ApprovalRequestResponse {
  id: string;
  organization_id: string;
  workflow_run_id: string;
  workflow_step_id: string;
  approval_type: ApprovalType;
  status: ApprovalStatus;
  reason: string;
  requested_by_agent: string;
  approved_by_user: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRequestListResponse {
  approvals: ApprovalRequestResponse[];
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: "ok";
  service: string;
  environment: string;
  version: string;
}

// ---------------------------------------------------------------------------
// Analytics (Milestone B)
// ---------------------------------------------------------------------------

export interface AnalyticsSummaryResponse {
  workflows_total: number;
  workflows_by_status: Record<string, number>;
  workflows_by_request_type: Record<string, number>;
  appointments_total: number;
  appointments_by_status: Record<string, number>;
  approvals_total: number;
  approvals_by_status: Record<string, number>;
  patients_total: number;
  documents_total: number;
  documents_by_status: Record<string, number>;
  tool_invocations_total: number;
  agent_handoffs_total: number;
  agent_handoffs_by_target: Record<string, number>;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Workflow event stream (Milestone B) — the Server-Sent Events payload
// shape from `GET .../workflows/{id}/events/stream`. Identical to
// `WorkflowTimelineEntry` (the backend serializes the SAME
// `_build_timeline` entries into each `event: workflow_event` message) —
// reused here rather than duplicated, since it IS the same wire shape.
// ---------------------------------------------------------------------------

export type WorkflowStreamEntry = WorkflowTimelineEntry;

export interface WorkflowStreamDone {
  status: WorkflowStatus;
}
