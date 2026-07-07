{{/* Common template helpers for helm-proxy. */}}

{{- define "helm-proxy.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "helm-proxy.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "helm-proxy.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "helm-proxy.labels" -}}
helm.sh/chart: {{ include "helm-proxy.chart" . }}
{{ include "helm-proxy.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "helm-proxy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "helm-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "helm-proxy.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "helm-proxy.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret holding git credentials / refresh token. */}}
{{- define "helm-proxy.secretName" -}}
{{- printf "%s-secret" (include "helm-proxy.fullname" .) -}}
{{- end -}}

{{/* True when a Secret is needed. */}}
{{- define "helm-proxy.needsSecret" -}}
{{- if or .Values.gitCredentials .Values.refreshToken -}}true{{- end -}}
{{- end -}}
