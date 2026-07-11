// Cosmos DB for NoSQL account + database + container with a HIERARCHICAL partition key
// (/year/month/day). Provisioned autoscale throughput (NOT serverless) so we can burst
// high enough to force multiple physical partitions, then scale down after loading.

@description('Location for all resources.')
param location string = resourceGroup().location

@description('Globally-unique Cosmos DB account name.')
param accountName string = 'cosmos-hpk-${uniqueString(resourceGroup().id)}'

@description('SQL (NoSQL API) database name.')
param databaseName string = 'ordersdb'

@description('Container name.')
param containerName string = 'orders'

@description('Autoscale max throughput (RU/s). Each 10000 RU/s ~= one physical partition: 30000 => ~3 partitions (a legible routing gradient). Bump to 40000+ for more.')
@minValue(1000)
@maxValue(100000)
param maxThroughput int = 30000

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [] // empty => provisioned throughput (serverless would add EnableServerless)
    disableKeyBasedMetadataWriteAccess: false
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: [
          '/year'
          '/month'
          '/day'
        ]
        kind: 'MultiHash' // MultiHash == hierarchical partition key
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: maxThroughput
      }
    }
  }
}

output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = databaseName
output containerName string = containerName
