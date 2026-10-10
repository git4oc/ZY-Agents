import { computed, type Ref } from 'vue'
import type { AgentCapabilityKey, AgentPolicySnapshot, TenantCandidate, UserProfile } from '../api/auth'

export function agentCapabilityAllowed(profile: UserProfile | null, key: AgentCapabilityKey): boolean {
  return profile?.agentPolicy ? profile.agentPolicy.capabilities[key] !== false : true
}

export function agentResourceFamilyAvailable(
  policy: AgentPolicySnapshot | null | undefined,
  family: 'skill' | 'tool',
): boolean {
  if (!policy) return true
  const mode = family === 'skill' ? policy.skillAccessMode : policy.toolAccessMode
  // An absent mode signals that this deployment follows the newer per-resource
  // permission contract, where the resource APIs take care of filtering.
  if (!mode || mode === 'all') return true
  const ids = family === 'skill' ? policy.skillIds : policy.toolIds
  return Array.isArray(ids) && ids.length > 0
}

export function tenantAdminAllowed(tenant: TenantCandidate): boolean {
  return tenant.spaceType === 'enterprise' && tenant.canAccessAdmin === true
}

export function personalSpaceActionsAllowed(profile: UserProfile | null): boolean {
  return profile?.spaceType === 'personal'
}

export function useEnterpriseAccessPolicy(profile: Ref<UserProfile | null>) {
  const agentPolicy = computed(() => profile.value?.agentPolicy)
  const capabilityEnabled = (key: AgentCapabilityKey) => computed(() => {
    return agentCapabilityAllowed(profile.value, key)
  })
  const canUseSkills = computed(() => {
    return agentResourceFamilyAvailable(agentPolicy.value, 'skill')
  })
  const canUseTools = computed(() => {
    return agentResourceFamilyAvailable(agentPolicy.value, 'tool')
  })
  const isEnterpriseSpace = computed(() => profile.value?.spaceType === 'enterprise')
  const isCommunity = computed(() => profile.value?.edition === 'community')
  const canCreateOrganization = computed(() => personalSpaceActionsAllowed(profile.value) && !isCommunity.value)
  const canUpgradePlan = computed(() => personalSpaceActionsAllowed(profile.value) && !isCommunity.value && profile.value?.billingEnabled !== false)

  return {
    canUseCode: capabilityEnabled('code_generation'),
    canUseBrowser: capabilityEnabled('browser_automation'),
    canUseKnowledge: capabilityEnabled('internal_knowledge'),
    canUseSkills,
    canUseTools,
    canAccessAdmin: tenantAdminAllowed,
    isEnterpriseSpace,
    canCreateOrganization,
    canUpgradePlan,
  }
}
